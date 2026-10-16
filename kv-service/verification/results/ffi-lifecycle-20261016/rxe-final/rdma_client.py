from __future__ import annotations

# Python RDMA client — ctypes wrapper for contextstore-rdma-ffi (Rust cdylib).
#
# Usage:
#     from contextstore.storage.rdma_client import RdmaClient
#
#     client = RdmaClient(
#         server_addr="127.0.0.1:50053",
#         device="mlx5_0", port=1, gid_index=3,
#         buf_size_mb=512,
#     )
#     client.connect()
#     n = client.get("rust-bench:rdma_test0:__combined__")
#     if n > 0:
#         # n bytes have been RDMA WRITE'd into client.buffer (zero-copy view)
#         data: bytes = client.buffer_view(n)
#     client.close()

import ctypes
import ctypes.util
import logging
import math
import os
from pathlib import Path
from threading import Lock

logger = logging.getLogger(__name__)


def _uint(value: int, bits: int, field: str, minimum: int = 0) -> int:
    """Reject ctypes' silent integer narrowing, including bool-as-int values."""
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value < 2**bits:
        raise ValueError(f"{field} must be an integer in [{minimum}, {2**bits})")
    return value


def _c_string(value: str, field: str, wire_string: bool = True) -> bytes:
    """Encode one complete C string without truncation or wire-length aliases."""
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a string")
    if "\x00" in value:
        raise ValueError(f"{field} must not contain NUL")
    encoded = value.encode("utf-8")
    if wire_string and len(encoded) > 65535:
        raise ValueError(f"{field} exceeds the uint16 wire byte limit")
    return encoded


def _descriptor_arguments(descriptor) -> tuple:
    """Capture mutable descriptor fields once, then validate exactly that snapshot."""
    handle, generation, etag, layout, size, striped, stripes, chunk = (
        getattr(descriptor, field)
        for field in (
            "object_handle",
            "object_generation",
            "content_etag",
            "layout_version",
            "size",
            "is_striped",
            "stripe_count",
            "chunk_size",
        )
    )
    if not isinstance(striped, bool):
        raise ValueError("is_striped must be a bool")
    return (
        _c_string(handle, "object_handle"),
        ctypes.c_uint64(_uint(generation, 64, "object_generation")),
        _c_string(etag, "content_etag"),
        ctypes.c_uint64(_uint(layout, 64, "layout_version")),
        ctypes.c_uint64(_uint(size, 64, "size")),
        ctypes.c_uint8(int(striped)),
        ctypes.c_uint32(_uint(stripes, 32, "stripe_count")),
        ctypes.c_uint64(_uint(chunk, 64, "chunk_size")),
    )


def _find_lib() -> str:
    """Search for contextstore_rdma_ffi.so. Prefer env var, then cargo target, then system path."""
    # 1) explicit env var
    env_path = os.environ.get("CONTEXTSTORE_RDMA_LIB")
    if env_path and Path(env_path).is_file():
        return env_path
    # 2) common cargo target paths (persistent / remote environments)
    candidates = [
        # Persistent PVC paths (preferred, survive container restarts)
        Path("/data/cs-build/rdma-ffi-target/release/libcontextstore_rdma_ffi.so"),
        Path("/data/cs-build/target/release/libcontextstore_rdma_ffi.so"),
        # Legacy transient cargo target (compatibility)
        Path("/root/cs-build/rdma-ffi-target/release/libcontextstore_rdma_ffi.so"),
        Path("/root/cs-build/target/release/libcontextstore_rdma_ffi.so"),
        # workspace-relative (for dev)
        Path(__file__).parent.parent.parent.parent
        / "kv-service/rdma-ffi/target/release/libcontextstore_rdma_ffi.so",
    ]
    for c in candidates:
        if c.is_file():
            return str(c)
    # 3) system search
    found = ctypes.util.find_library("contextstore_rdma_ffi")
    if found:
        return found
    raise FileNotFoundError(
        "libcontextstore_rdma_ffi.so not found. Build it with: "
        "cd kv-service/rdma-ffi && cargo build --release. "
        "Or set env CONTEXTSTORE_RDMA_LIB=/path/to/libcontextstore_rdma_ffi.so"
    )


class RdmaClient:
    """Single-connection client with serialized native operations and teardown.

    Callers must still serialize GET plus buffer consumption, and keep raw
    views and externally registered allocations alive for their documented
    lifetimes. A transfer error requires reconnect before the next transfer.

    The native library must advertise lifecycle safety version 1 or newer.
    An explicit timeout also requires native deadline support. With timeout=None,
    use the library's default (30 seconds in current builds, or unbounded if it
    lacks deadline support, reported by control_timeout=None). The timeout covers
    TCP operations after hostname resolution, not DNS resolution.
    """

    def __init__(
        self,
        server_addr: str,
        device: str = "mlx5_0",
        port: int = 1,
        gid_index: int = 3,
        buf_size_mb: int = 512,
        timeout: float | None = None,
    ) -> None:
        if timeout is not None and (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout * 1000 >= 2**64
        ):
            raise ValueError("timeout must be a positive finite number of seconds")
        _uint(buf_size_mb, 44, "buf_size_mb", minimum=1)
        _uint(port, 8, "port", minimum=1)
        _uint(gid_index, 8, "gid_index")
        encoded_device = _c_string(device, "device", wire_string=False)
        _c_string(server_addr, "server_addr", wire_string=False)
        self._lock = Lock()
        self._handle = None
        self._connected = False
        self.server_addr = server_addr
        self.device = device
        self.port = port
        self.gid_index = gid_index
        self.buf_size = buf_size_mb * 1024 * 1024

        lib_path = _find_lib()
        logger.info("RdmaClient: loading lib from %s", lib_path)
        self._lib = ctypes.CDLL(lib_path, use_errno=True)
        self._setup_prototypes()
        self._control_timeout = (
            (30.0 if timeout is None else timeout) if self._has_timeout else None
        )

        # Create client (handle is an opaque void*)
        self._handle = self._lib.cs_rdma_client_new(
            encoded_device,
            ctypes.c_uint8(port),
            ctypes.c_uint8(gid_index),
            ctypes.c_uint64(self.buf_size),
        )
        if not self._handle:
            raise RuntimeError(
                f"cs_rdma_client_new failed (device={device}, buf_size={self.buf_size})"
            )
        if self._has_timeout:
            rc = self._lib.cs_rdma_client_set_timeout_ms(
                self._handle, ctypes.c_uint64(max(1, int(self._control_timeout * 1000)))
            )
            if rc != 0:
                self.close()
                raise RuntimeError(f"set RDMA control timeout failed: rc={rc}")
        elif timeout is not None:
            self.close()
            raise RuntimeError("older rdma-ffi does not support control deadlines, please rebuild")

    @property
    def control_timeout(self) -> float | None:
        """Native TCP timeout after DNS, or None when deadline support is absent."""
        return self._control_timeout

    def _require_safe_lifecycle(self) -> None:
        """Reject native libraries that can return while DMA remains active."""
        version = getattr(self._lib, "cs_rdma_client_lifecycle_version", None)
        if version is not None:
            version.restype = ctypes.c_uint32
            version.argtypes = []
        if version is None or version() < 1:
            raise RuntimeError(
                "native rdma-ffi does not provide lifecycle safety version 1; please rebuild"
            )

    def _setup_prototypes(self) -> None:
        self._require_safe_lifecycle()
        L = self._lib
        L.cs_rdma_client_new.restype = ctypes.c_void_p
        L.cs_rdma_client_new.argtypes = [
            ctypes.c_char_p,
            ctypes.c_uint8,
            ctypes.c_uint8,
            ctypes.c_uint64,
        ]
        L.cs_rdma_client_connect.restype = ctypes.c_int
        L.cs_rdma_client_connect.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.cs_rdma_client_get.restype = ctypes.c_int64
        L.cs_rdma_client_get.argtypes = [ctypes.c_void_p, ctypes.c_char_p]
        L.cs_rdma_client_buffer.restype = ctypes.POINTER(ctypes.c_uint8)
        L.cs_rdma_client_buffer.argtypes = [ctypes.c_void_p]
        L.cs_rdma_client_buffer_size.restype = ctypes.c_uint64
        L.cs_rdma_client_buffer_size.argtypes = [ctypes.c_void_p]
        L.cs_rdma_client_free.restype = None
        L.cs_rdma_client_free.argtypes = [ctypes.c_void_p]
        self._has_timeout = hasattr(L, "cs_rdma_client_set_timeout_ms")
        if self._has_timeout:
            L.cs_rdma_client_set_timeout_ms.restype = ctypes.c_int32
            L.cs_rdma_client_set_timeout_ms.argtypes = [ctypes.c_void_p, ctypes.c_uint64]
        # ===== Plan A zero-copy interface (added by rdma-ffi, absent in older .so) =====
        self._has_zerocopy = hasattr(L, "cs_rdma_client_register_external_buffer")
        if self._has_zerocopy:
            L.cs_rdma_client_register_external_buffer.restype = ctypes.c_int32
            L.cs_rdma_client_register_external_buffer.argtypes = [
                ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_uint8),
                ctypes.c_uint64,
            ]
            L.cs_rdma_client_get_into.restype = ctypes.c_int64
            L.cs_rdma_client_get_into.argtypes = [
                ctypes.c_void_p,
                ctypes.c_int32,
                ctypes.c_char_p,
                ctypes.c_uint64,
            ]
            L.cs_rdma_client_unregister_external_buffer.restype = ctypes.c_int32
            L.cs_rdma_client_unregister_external_buffer.argtypes = [ctypes.c_void_p, ctypes.c_int32]
        self._has_descriptor_get_into = hasattr(L, "cs_rdma_client_get_descriptor_into")
        if self._has_descriptor_get_into:
            L.cs_rdma_client_get_descriptor_into.restype = ctypes.c_int64
            L.cs_rdma_client_get_descriptor_into.argtypes = [
                ctypes.c_void_p,  # client
                ctypes.c_int32,  # region_id
                ctypes.c_char_p,  # key
                ctypes.c_char_p,  # object_handle
                ctypes.c_uint64,  # object_generation
                ctypes.c_char_p,  # content_etag
                ctypes.c_uint64,  # layout_version
                ctypes.c_uint64,  # size
                ctypes.c_uint8,  # is_striped
                ctypes.c_uint32,  # stripe_count
                ctypes.c_uint64,  # chunk_size
                ctypes.c_uint64,  # offset
            ]
        self._has_descriptor_get = hasattr(L, "cs_rdma_client_get_descriptor")
        if self._has_descriptor_get:
            L.cs_rdma_client_get_descriptor.restype = ctypes.c_int64
            L.cs_rdma_client_get_descriptor.argtypes = [
                ctypes.c_void_p,  # client
                ctypes.c_char_p,  # key
                ctypes.c_char_p,  # object_handle
                ctypes.c_uint64,  # object_generation
                ctypes.c_char_p,  # content_etag
                ctypes.c_uint64,  # layout_version
                ctypes.c_uint64,  # size
                ctypes.c_uint8,  # is_striped
                ctypes.c_uint32,  # stripe_count
                ctypes.c_uint64,  # chunk_size
            ]
        # ===== PUT data plane (further additions in rdma-ffi, absent in older .so) =====
        self._has_put = hasattr(L, "cs_rdma_client_put")
        if self._has_put:
            L.cs_rdma_client_put.restype = ctypes.c_int32
            L.cs_rdma_client_put.argtypes = [
                ctypes.c_void_p,  # client
                ctypes.c_int32,  # region_id
                ctypes.c_char_p,  # key
                ctypes.c_uint64,  # offset
                ctypes.c_uint64,  # size
            ]

    def _require_open(self, connected: bool = False) -> None:
        """Validate state while the caller holds the handle's lifecycle lock."""
        if not self._handle:
            raise RuntimeError("RDMA client is closed")
        if connected and not self._connected:
            raise RuntimeError("not connected — call connect() first")

    def connect(self) -> None:
        with self._lock:
            self._require_open()
            if self._connected:
                return
            address = _c_string(self.server_addr, "server_addr", wire_string=False)
            rc = self._lib.cs_rdma_client_connect(self._handle, address)
            if rc != 0:
                self._connected = False
                raise RuntimeError(f"cs_rdma_client_connect failed: rc={rc}")
            self._connected = True
        logger.info("RdmaClient connected to %s", self.server_addr)

    def get(self, key: str) -> int:
        """Send GET and block until server WRITE completes. Returns bytes written; 0 = miss; exception = error.

        Written data starts at offset 0 of the buffer, length equals the return value.
        """
        with self._lock:
            self._require_open(connected=True)
            n = self._lib.cs_rdma_client_get(self._handle, _c_string(key, "key"))
            if n < 0:
                self._connected = False
                raise RuntimeError(f"cs_rdma_client_get failed: rc={n}")
            return int(n)

    def buffer_ptr(self) -> int:
        """Returns the buffer start address (int). Advanced usage (numpy / torch frombuffer)."""
        with self._lock:
            self._require_open()
            ptr = self._lib.cs_rdma_client_buffer(self._handle)
            return ctypes.cast(ptr, ctypes.c_void_p).value or 0

    def buffer_view(self, length: int) -> bytes:
        """Copy `length` bytes out of the buffer (memcpy). Simple but incurs one copy.

        Uses ctypes.string_at, slightly faster than bytes(memoryview(...)).
        """
        with self._lock:
            self._require_open()
            if _uint(length, 64, "length") > self.buf_size:
                raise ValueError("length exceeds the built-in RDMA buffer")
            ptr = self._lib.cs_rdma_client_buffer(self._handle)
            return ctypes.string_at(ptr, length)

    def buffer_memview(self, length: int) -> memoryview:
        """Borrow the native buffer; do not retain the view across transfers or close()."""
        with self._lock:
            self._require_open()
            if _uint(length, 64, "length") > self.buf_size:
                raise ValueError("length exceeds the built-in RDMA buffer")
            ptr = self._lib.cs_rdma_client_buffer(self._handle)
            arr_type = ctypes.c_uint8 * length
            arr = arr_type.from_address(ctypes.addressof(ptr.contents))
            return memoryview(arr)

    # ===== Plan A zero-copy interface =====
    @property
    def supports_zerocopy(self) -> bool:
        """Whether the rdma-ffi .so provides the zero-copy interface (register_external + get_into)."""
        return self._has_zerocopy

    def register_external_buffer(self, ptr: int, size: int) -> int:
        """Register a caller-owned pinned host buffer as an RDMA MR. Returns region_id (>=0).

        Args:
            ptr: memory start address (int, typically from `numpy.ndarray.ctypes.data` or `posix_memalign`)
            size: buffer size in bytes

        Caller must ensure:
        - Memory is 4KB page-aligned (recommend `posix_memalign(align=4096)`)
        - Already mlock'd or cudaHostAlloc'd, else reg_mr triggers implicit pin (slow, consumes RLIMIT_MEMLOCK)
        - Lifetime ≥ until unregister call

        Typical usage (numpy + mlock):
            arr = np.zeros(N, dtype=np.uint8)  # Note: default is not page-aligned
            # Or use posix_memalign for a page-aligned block
            region_id = client.register_external_buffer(arr.ctypes.data, arr.nbytes)
        """
        if not self._has_zerocopy:
            raise RuntimeError(
                "older rdma-ffi does not support zero-copy interface, please rebuild"
            )
        pointer_bits = ctypes.sizeof(ctypes.c_void_p) * 8
        _uint(ptr, pointer_bits, "ptr", minimum=1)
        _uint(size, pointer_bits, "size", minimum=1)
        if ptr + size > 2**pointer_bits:
            raise ValueError("external buffer address range overflows the native pointer")
        with self._lock:
            self._require_open()
            ptr_u8 = ctypes.cast(ptr, ctypes.POINTER(ctypes.c_uint8))
            rid = self._lib.cs_rdma_client_register_external_buffer(
                self._handle, ptr_u8, ctypes.c_uint64(size)
            )
            if rid < 0:
                raise RuntimeError(f"register_external_buffer failed: rc={rid}")
            return int(rid)

    def get_into(self, region_id: int, key: str, offset: int = 0) -> int:
        """RDMA GET; server WRITE goes directly to buffer at `region_id` starting at `offset`.

        Returns bytes written; 0 = miss; exception = error.

        Zero-copy: after data is written, Python can directly numpy/torch frombuffer view the memory without memcpy or GIL hold.
        """
        if not self._has_zerocopy:
            raise RuntimeError(
                "older rdma-ffi does not support zero-copy interface, please rebuild"
            )
        _uint(region_id, 31, "region_id")
        _uint(offset, 64, "offset")
        with self._lock:
            self._require_open(connected=True)
            n = self._lib.cs_rdma_client_get_into(
                self._handle,
                ctypes.c_int32(region_id),
                _c_string(key, "key"),
                ctypes.c_uint64(offset),
            )
            if n < 0:
                self._connected = False
                raise RuntimeError(f"get_into failed: rc={n}")
            return int(n)

    @property
    def supports_descriptor_get(self) -> bool:
        """Whether the rdma-ffi .so provides the descriptor GET interface."""
        return self._has_descriptor_get and self._has_descriptor_get_into

    def get_descriptor_into(self, region_id: int, key: str, descriptor, offset: int = 0) -> int:
        """RDMA descriptor GET; server writes the descriptor-specified version into the external buffer."""
        if not self._has_descriptor_get_into:
            raise RuntimeError(
                "older rdma-ffi does not support descriptor GET interface, please rebuild"
            )
        _uint(region_id, 31, "region_id")
        _uint(offset, 64, "offset")
        with self._lock:
            self._require_open(connected=True)
            n = self._lib.cs_rdma_client_get_descriptor_into(
                self._handle,
                ctypes.c_int32(region_id),
                _c_string(key, "key"),
                *_descriptor_arguments(descriptor),
                ctypes.c_uint64(offset),
            )
            if n < 0:
                self._connected = False
                raise RuntimeError(f"get_descriptor_into failed: rc={n}")
            return int(n)

    def get_descriptor(self, key: str, descriptor) -> int:
        """RDMA descriptor GET; server writes the descriptor-specified version into the client's built-in buffer."""
        if not self._has_descriptor_get:
            raise RuntimeError(
                "older rdma-ffi does not support descriptor GET interface, please rebuild"
            )
        with self._lock:
            self._require_open(connected=True)
            n = self._lib.cs_rdma_client_get_descriptor(
                self._handle,
                _c_string(key, "key"),
                *_descriptor_arguments(descriptor),
            )
            if n < 0:
                self._connected = False
                raise RuntimeError(f"get_descriptor failed: rc={n}")
            return int(n)

    def unregister_external_buffer(self, region_id: int) -> None:
        """Unregister (dereg_mr). Caller is responsible for freeing memory."""
        if not self._has_zerocopy:
            return
        _uint(region_id, 31, "region_id")
        with self._lock:
            self._require_open()
            rc = self._lib.cs_rdma_client_unregister_external_buffer(
                self._handle, ctypes.c_int32(region_id)
            )
            if rc != 0:
                raise RuntimeError(f"unregister_external_buffer failed: rc={rc}")

    # ===== PUT data plane =====
    @property
    def supports_put(self) -> bool:
        """Whether the rdma-ffi .so provides the PUT interface."""
        return self._has_put

    def put(self, region_id: int, key: str, offset: int, size: int) -> None:
        """RDMA PUT: push [offset, offset+size) from the pre-registered buffer to the server; returns after server persists.

        Args:
            region_id: region id returned by register_external_buffer
            key: ObjectKey canonical string, format "<namespace_byte_len>:<namespace><object_key>"
            offset: offset within the buffer
            size: bytes to push

        Raises:
            RuntimeError: older rdma-ffi does not support / not connected / RDMA WRITE failed / server persist failed
        """
        if not self._has_put:
            raise RuntimeError("older rdma-ffi does not support PUT interface, please rebuild")
        _uint(region_id, 31, "region_id")
        _uint(offset, 64, "offset")
        _uint(size, 64, "size", minimum=1)
        if offset + size >= 2**64:
            raise ValueError("PUT offset plus size exceeds uint64 range")
        with self._lock:
            self._require_open(connected=True)
            rc = self._lib.cs_rdma_client_put(
                self._handle,
                ctypes.c_int32(region_id),
                _c_string(key, "key"),
                ctypes.c_uint64(offset),
                ctypes.c_uint64(size),
            )
            if rc != 0:
                self._connected = False
                raise RuntimeError(f"cs_rdma_client_put failed: rc={rc}")

    def close(self) -> None:
        with self._lock:
            if self._handle:
                self._connected = False
                handle = self._handle
                self._handle = None
                # ctypes may deliver KeyboardInterrupt after native free returns.
                # Detach first so close()/__del__ cannot retry a dangling pointer.
                self._lib.cs_rdma_client_free(handle)

    def __enter__(self) -> "RdmaClient":
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass
