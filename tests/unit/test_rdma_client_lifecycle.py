from __future__ import annotations

from threading import Event, Lock, Thread
from types import SimpleNamespace

import pytest

from contextstore.storage.rdma_client import RdmaClient
from contextstore.storage import rdma_client as rdma_module


class FakeNative:
    def __init__(self) -> None:
        self.started = Event()
        self.release = Event()
        self.freed = Event()
        self.unregistered = Event()
        self.read_result = 4
        self.unregister_result = 0

    def cs_rdma_client_get_into(self, *args) -> int:
        self.started.set()
        assert self.release.wait(2)
        return self.read_result

    def cs_rdma_client_get(self, *args) -> int:
        return self.read_result

    def cs_rdma_client_connect(self, *args) -> int:
        return 0

    def cs_rdma_client_free(self, *args) -> None:
        self.freed.set()

    def cs_rdma_client_unregister_external_buffer(self, *args) -> int:
        self.unregistered.set()
        return self.unregister_result

    def cs_rdma_client_register_external_buffer(self, *args) -> int:
        return 0


def client_with(native: FakeNative) -> RdmaClient:
    client = RdmaClient.__new__(RdmaClient)
    client._lib = native
    client._handle = 123
    client._connected = True
    client._has_zerocopy = True
    client._lock = Lock()
    client.server_addr = "127.0.0.1:50053"
    return client


@pytest.mark.parametrize("teardown", ["close", "unregister"])
def test_native_teardown_waits_for_active_external_read(teardown: str) -> None:
    native = FakeNative()
    client = client_with(native)
    errors = []

    def read() -> None:
        try:
            assert client.get_into(0, "key") == 4
        except Exception as exc:
            errors.append(exc)

    reader = Thread(target=read)
    reader.start()
    assert native.started.wait(1)
    entered = Event()

    def finish() -> None:
        entered.set()
        if teardown == "close":
            client.close()
        else:
            client.unregister_external_buffer(0)

    closer = Thread(target=finish)
    closer.start()
    assert entered.wait(1)
    event = native.freed if teardown == "close" else native.unregistered
    try:
        assert not event.wait(0.1), "native resource released while get_into is active"
    finally:
        native.release.set()
        reader.join(2)
        closer.join(2)
    assert not errors
    assert not reader.is_alive() and not closer.is_alive()
    assert event.is_set()


def test_failed_native_read_requires_explicit_reconnect() -> None:
    native = FakeNative()
    native.read_result = -3
    client = client_with(native)
    with pytest.raises(RuntimeError):
        client.get("key")
    assert not client._connected
    client.connect()
    native.read_result = 4
    assert client.get("key") == 4


def test_failed_unregister_is_reported_to_owner() -> None:
    native = FakeNative()
    native.unregister_result = -2
    client = client_with(native)
    with pytest.raises(RuntimeError, match="unregister"):
        client.unregister_external_buffer(0)


def test_closed_client_rejects_external_registration() -> None:
    client = client_with(FakeNative())
    client.close()
    with pytest.raises(RuntimeError, match="closed"):
        client.register_external_buffer(4096, 4096)


def native_library(monkeypatch, lifecycle_version: int | None = 1) -> SimpleNamespace:
    allocated = Event()
    freed = Event()

    class Function:
        def __init__(self, result=None, callback=None):
            self.result = result
            self.callback = callback

        def __call__(self, *args):
            if self.callback:
                self.callback()
            return self.result

    library = SimpleNamespace(
        cs_rdma_client_new=Function(123, callback=allocated.set),
        cs_rdma_client_connect=Function(0),
        cs_rdma_client_get=Function(0),
        cs_rdma_client_buffer=Function(None),
        cs_rdma_client_buffer_size=Function(4096),
        cs_rdma_client_free=Function(callback=freed.set),
    )
    if lifecycle_version is not None:
        library.cs_rdma_client_lifecycle_version = Function(lifecycle_version)
    monkeypatch.setattr(rdma_module, "_find_lib", lambda: "fake.so")
    monkeypatch.setattr(rdma_module.ctypes, "CDLL", lambda *args, **kwargs: library)
    return SimpleNamespace(allocated=allocated, freed=freed)


@pytest.mark.parametrize("lifecycle_version", [None, 0])
def test_unsafe_native_library_is_rejected_before_allocation(
    monkeypatch, lifecycle_version: int | None
) -> None:
    events = native_library(monkeypatch, lifecycle_version)
    with pytest.raises(RuntimeError, match="lifecycle safety"):
        RdmaClient("127.0.0.1:50053", buf_size_mb=1)
    assert not events.allocated.is_set()
    assert not events.freed.is_set()


def test_lifecycle_safe_library_without_deadlines_reports_unbounded_io(monkeypatch) -> None:
    native_library(monkeypatch)
    client = RdmaClient("127.0.0.1:50053", buf_size_mb=1)
    assert client.control_timeout is None
    client.close()


def test_explicit_deadline_requires_support_even_at_30_seconds(monkeypatch) -> None:
    events = native_library(monkeypatch)
    with pytest.raises(RuntimeError, match="control deadlines"):
        RdmaClient("127.0.0.1:50053", buf_size_mb=1, timeout=30.0)
    assert events.freed.is_set()


def descriptor_client():
    native = FakeNative()
    native.descriptor_calls = []
    native.cs_rdma_client_get_descriptor = lambda *args: native.descriptor_calls.append(args) or 4
    native.cs_rdma_client_get_descriptor_into = (
        lambda *args: native.descriptor_calls.append(args) or 4
    )
    client = client_with(native)
    client._has_descriptor_get = True
    client._has_descriptor_get_into = True
    return client, native


def valid_descriptor():
    return SimpleNamespace(
        object_handle="handle",
        object_generation=1,
        content_etag="etag",
        layout_version=1,
        size=4,
        is_striped=False,
        stripe_count=1,
        chunk_size=4,
    )


@pytest.mark.parametrize("external", [False, True])
@pytest.mark.parametrize(
    "field,value",
    [
        ("object_generation", -1),
        ("object_generation", 2**64 + 1),
        ("layout_version", 2**64),
        ("size", -1),
        ("chunk_size", 2**64),
        ("stripe_count", 2**32 + 1),
        ("stripe_count", True),
        ("is_striped", 1),
        ("object_handle", "handle\x00alias"),
        ("content_etag", "etag\x00alias"),
        ("object_handle", "界" * 22000),
    ],
)
def test_descriptor_rejects_aliasing_values_before_native_call(external, field, value):
    client, native = descriptor_client()
    descriptor = valid_descriptor()
    setattr(descriptor, field, value)
    with pytest.raises(ValueError):
        if external:
            client.get_descriptor_into(0, "key", descriptor)
        else:
            client.get_descriptor("key", descriptor)
    assert native.descriptor_calls == []
    assert client._connected


@pytest.mark.parametrize(
    "method", ["get", "get_into", "put", "get_descriptor", "get_descriptor_into"]
)
def test_embedded_nul_key_is_rejected_without_disconnect(method):
    client, native = descriptor_client()
    client._has_put = True
    native.cs_rdma_client_put = lambda *args: 0
    native.release.set()
    arguments = {
        "get": ("key\x00alias",),
        "get_into": (0, "key\x00alias"),
        "put": (0, "key\x00alias", 0, 4),
        "get_descriptor": ("key\x00alias", valid_descriptor()),
        "get_descriptor_into": (0, "key\x00alias", valid_descriptor()),
    }
    with pytest.raises(ValueError, match="NUL"):
        getattr(client, method)(*arguments[method])
    assert client._connected
    assert native.descriptor_calls == []
    assert not native.started.is_set()


@pytest.mark.parametrize(
    "field,value",
    [
        ("port", -1),
        ("port", 257),
        ("gid_index", 256),
        ("gid_index", True),
        ("device", "dev\x00alias"),
        ("server_addr", "host\x00alias"),
    ],
)
def test_constructor_rejects_narrowing_before_native_allocation(monkeypatch, field, value):
    events = native_library(monkeypatch)
    kwargs = {"server_addr": "127.0.0.1:50053", "buf_size_mb": 1, field: value}
    with pytest.raises(ValueError):
        RdmaClient(**kwargs)
    assert not events.allocated.is_set()


@pytest.mark.parametrize(
    "region,offset,size", [(2**31, 0, 1), (True, 0, 1), (0, True, 1), (0, 0, True)]
)
def test_put_rejects_non_integer_or_wrapped_ranges(region, offset, size):
    client, native = descriptor_client()
    client._has_put = True
    called = []
    native.cs_rdma_client_put = lambda *args: called.append(args) or 0
    with pytest.raises(ValueError):
        client.put(region, "key", offset, size)
    assert not called


def test_interrupted_native_free_cannot_leave_a_dangling_handle():
    native = FakeNative()
    client = client_with(native)
    calls = []

    def free_then_interrupt(handle):
        calls.append(handle)
        raise KeyboardInterrupt()

    native.cs_rdma_client_free = free_then_interrupt
    with pytest.raises(KeyboardInterrupt):
        client.close()
    assert client._handle is None
    assert not client._connected
    client.close()
    assert calls == [123]


@pytest.mark.parametrize("external", [False, True])
def test_descriptor_uses_the_validated_snapshot_once(external):
    client, native = descriptor_client()
    values = vars(valid_descriptor())
    counts = {}

    class ReadOnceDescriptor:
        def __getattr__(self, field):
            counts[field] = counts.get(field, 0) + 1
            assert counts[field] == 1, "descriptor field was read again after validation"
            return values[field]

    if external:
        assert client.get_descriptor_into(0, "key", ReadOnceDescriptor()) == 4
    else:
        assert client.get_descriptor("key", ReadOnceDescriptor()) == 4
    assert counts == dict.fromkeys(values, 1)
    args = native.descriptor_calls[0]
    identity = args[3:11] if external else args[2:10]
    assert identity[0] == b"handle"
    assert identity[1].value == 1
    assert identity[2] == b"etag"
    assert [value.value for value in identity[3:]] == [1, 4, 0, 1, 4]
