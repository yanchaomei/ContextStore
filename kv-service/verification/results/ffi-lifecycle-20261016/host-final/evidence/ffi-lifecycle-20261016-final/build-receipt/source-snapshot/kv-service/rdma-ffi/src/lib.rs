//! ContextStore RDMA Client — C ABI cdylib.
//!
//! Python ctypes loads this .so directly and calls the following interface:
//!
//! ```c
//! // Create a client; opens the HCA, allocates PD/CQ, allocates and reg_mrs a buffer.
//! void* cs_rdma_client_new(const char* device, uint8_t port, uint8_t gid_index, uint64_t buf_size);
//!
//! // TCP connect to the server, negotiate QP, transition INIT->RTR->RTS.
//! int cs_rdma_client_connect(void* client, const char* server_addr);
//! // Optional override; zero is rejected. Default: 30 seconds per control I/O
//! // after hostname resolution (DNS itself is not bounded by this setting).
//! int32_t cs_rdma_client_set_timeout_ms(void* client, uint64_t timeout_ms);
//!
//! // ====== Classic interface: server WRITEs into the client's built-in buffer (needs
//! //        string_at to copy out, holding the GIL). ======
//! // RDMA GET: send request; server WRITEs into our buffer.
//! // Returns bytes_written; -1 = miss/error. Written at buffer + 0, length bytes_written.
//! int64_t cs_rdma_client_get(void* client, const char* key);
//!
//! // Get the buffer pointer (Python can use ctypes.string_at(ptr, n) or a numpy view).
//! const uint8_t* cs_rdma_client_buffer(void* client);
//!
//! // ====== Zero-copy interface (Plan A): server WRITEs directly into the caller-provided
//! //        pinned buffer. ======
//! // Caller registers a pre-pinned host buffer (typically cudaHostAlloc + page-aligned);
//! // internally we ibv_reg_mr once and return region_id (>=0); failure returns <0.
//! // A single client may register multiple external buffers (e.g. a worker pool).
//! int32_t cs_rdma_client_register_external_buffer(void* client, const uint8_t* ptr, uint64_t size);
//!
//! // GET into a pre-registered external buffer. Server WRITEs N bytes into
//! // buffer[offset..offset+N]. Returns bytes_written; 0 = miss, <0 = error. Python gets
//! // it as a zero-copy view.
//! int64_t cs_rdma_client_get_into(void* client, int32_t region_id, const char* key, uint64_t offset);
//!
//! // Unregister an external buffer (dereg_mr). Caller is responsible for freeing memory.
//! int32_t cs_rdma_client_unregister_external_buffer(void* client, int32_t region_id);
//!
//! // Destroy the client (also unregisters all external regions + frees the built-in buffer).
//! void cs_rdma_client_free(void* client);
//! ```
//!
//! C callers must serialize every operation on a handle, including registration,
//! unregistration and free. After a failed transfer the QP is retired before the
//! error is returned; reconnect is required. A provider cleanup error aborts the
//! process before registered memory can be freed or reused. External-buffer GETs
//! may contain partial data on failure; this ABI does not provide atomic publication.

use anyhow::{anyhow, Result};
use rdma_sys::*;
use std::ffi::{c_char, c_int, CStr};
use std::io::{Read, Write};
use std::net::{Shutdown, TcpStream, ToSocketAddrs};
use std::os::raw::c_void;
use std::ptr::{self, NonNull};
use std::time::{Duration, Instant};

const DEFAULT_CONTROL_TIMEOUT: Duration = Duration::from_secs(30);

fn require_cleanup_success(rc: i32, message: &'static [u8]) {
    if rc != 0 {
        // A failed provider cleanup leaves DMA ownership uncertain. Logging
        // must not unwind before the process has stopped using that memory.
        unsafe { libc::write(libc::STDERR_FILENO, message.as_ptr().cast(), message.len()) };
        std::process::abort();
    }
}

fn report_error(operation: &str, error: &anyhow::Error) {
    let mut stderr = std::io::stderr().lock();
    let _ = writeln!(stderr, "[cs_rdma_ffi] {operation} failed: {error}");
}

fn read_get_response(
    stream: &mut TcpStream,
    capacity: u64,
    expected_size: Option<u64>,
) -> Result<u64> {
    let mut tag = [0u8; 1];
    stream.read_exact(&mut tag)?;
    if tag[0] != MSG_GET_RESP {
        return Err(anyhow!("expected GET_RESP, got {}", tag[0]));
    }
    let mut body = [0u8; 13];
    stream.read_exact(&mut body)?;
    let bytes = u64::from_le_bytes([
        body[1], body[2], body[3], body[4], body[5], body[6], body[7], body[8],
    ]);
    let chunks = u32::from_le_bytes([body[9], body[10], body[11], body[12]]);
    if body[0] > 1 || bytes > capacity || bytes > i64::MAX as u64 {
        return Err(anyhow!("invalid GET response flags or byte count"));
    }
    if body[0] == 0 {
        if bytes != 0 || chunks != 0 {
            return Err(anyhow!("missing GET response reports transferred data"));
        }
        return Ok(0);
    }
    if expected_size.is_some_and(|expected| bytes != expected) {
        return Err(anyhow!("descriptor GET returned an incomplete object"));
    }
    if bytes != 0 && chunks == 0 {
        return Err(anyhow!("GET response has no completed chunks"));
    }
    Ok(bytes)
}

// ===== Control-plane constants, matching the server-side wire.rs =====
const MSG_HELLO: u8 = 1;
const MSG_GET_REQ: u8 = 2;
const MSG_GET_RESP: u8 = 3;
// PUT data plane (client → server)
const MSG_PUT_REQ: u8 = 4;
const MSG_PUT_READY: u8 = 5;
const MSG_PUT_COMMIT: u8 = 6;
const MSG_PUT_RESP: u8 = 7;
const MSG_GET_DESCRIPTOR_REQ: u8 = 8;
const MSG_BYE: u8 = 99;

#[repr(C)]
struct QpInfo {
    qpn: u32,
    psn: u32,
    gid: ibv_gid,
}

impl QpInfo {
    fn to_bytes(&self) -> [u8; 24] {
        let mut buf = [0u8; 24];
        buf[0..4].copy_from_slice(&self.qpn.to_le_bytes());
        buf[4..8].copy_from_slice(&self.psn.to_le_bytes());
        unsafe {
            buf[8..24].copy_from_slice(&self.gid.raw[..]);
        }
        buf
    }
    fn from_bytes(buf: &[u8; 24]) -> Self {
        let qpn = u32::from_le_bytes([buf[0], buf[1], buf[2], buf[3]]);
        let psn = u32::from_le_bytes([buf[4], buf[5], buf[6], buf[7]]);
        let mut gid: ibv_gid = unsafe { std::mem::zeroed() };
        unsafe {
            gid.raw[..].copy_from_slice(&buf[8..24]);
        }
        Self { qpn, psn, gid }
    }
}

/// Externally pre-registered buffer (caller owns the memory; we only own the MR).
/// Note: bidirectional — used both as a GET target (server WRITEs in) and as a PUT
/// source (client WRITEs out). rkey is used when the server WRITEs in (GET); lkey is
/// used when the client WRITEs out (PUT, ibv_sge.lkey).
struct ExternalRegion {
    mr: NonNull<ibv_mr>,
    ptr: *const u8,
    size: usize,
    lkey: u32,
    rkey: u32,
}

unsafe impl Send for ExternalRegion {}

struct DeviceList(*mut *mut ibv_device);

impl Drop for DeviceList {
    fn drop(&mut self) {
        unsafe { ibv_free_device_list(self.0) };
    }
}

/// Roll back partial initialization; ownership moves to Client only on success.
#[derive(Default)]
struct InitResources {
    ctx: Option<NonNull<ibv_context>>,
    pd: Option<NonNull<ibv_pd>>,
    cq: Option<NonNull<ibv_cq>>,
    mr: Option<NonNull<ibv_mr>>,
    buffer: Option<(*mut u8, std::alloc::Layout)>,
}

impl Drop for InitResources {
    fn drop(&mut self) {
        unsafe {
            if let Some(mr) = self.mr.take() {
                require_cleanup_success(
                    ibv_dereg_mr(mr.as_ptr()),
                    b"ContextStore FFI init MR cleanup failed; aborting\n",
                );
            }
            if let Some((buffer, layout)) = self.buffer.take() {
                std::alloc::dealloc(buffer, layout);
            }
            if let Some(cq) = self.cq.take() {
                require_cleanup_success(
                    ibv_destroy_cq(cq.as_ptr()),
                    b"ContextStore FFI init CQ cleanup failed; aborting\n",
                );
            }
            if let Some(pd) = self.pd.take() {
                require_cleanup_success(
                    ibv_dealloc_pd(pd.as_ptr()),
                    b"ContextStore FFI init PD cleanup failed; aborting\n",
                );
            }
            if let Some(ctx) = self.ctx.take() {
                require_cleanup_success(
                    ibv_close_device(ctx.as_ptr()),
                    b"ContextStore FFI init device cleanup failed; aborting\n",
                );
            }
        }
    }
}

/// Internal client state (opaque to C).
struct Client {
    // Config
    port_num: u8,
    gid_index: u8,
    control_timeout: Duration,
    // Resources
    ctx: NonNull<ibv_context>,
    pd: NonNull<ibv_pd>,
    cq: NonNull<ibv_cq>,
    qp: Option<NonNull<ibv_qp>>,
    mr: NonNull<ibv_mr>,
    buf_ptr: *mut u8,
    buf_size: usize,
    buf_layout: std::alloc::Layout,
    local_gid: ibv_gid,
    local_qpn: u32,
    local_psn: u32,
    local_rkey: u32,
    // Control connection
    stream: Option<TcpStream>,
    // External pre-registered buffer pool (Plan A zero-copy). Vec index is the region_id.
    // Uses Vec<Option<>> so unregister leaves a hole rather than reindexing — existing
    // region_ids stay valid.
    external_regions: Vec<Option<ExternalRegion>>,
}

impl Client {
    fn new(device: &str, port: u8, gid_index: u8, buf_size: usize) -> Result<Self> {
        if buf_size == 0 {
            return Err(anyhow!("built-in buffer must have positive size"));
        }
        let mut resources = InitResources::default();
        unsafe {
            // ===== 1. Find device + open =====
            let mut num = 0i32;
            let dev_list = ibv_get_device_list(&mut num);
            if dev_list.is_null() {
                return Err(anyhow!("ibv_get_device_list returned null"));
            }
            let devices = DeviceList(dev_list);
            let mut dev_ptr: *mut ibv_device = ptr::null_mut();
            for i in 0..num {
                let d = *dev_list.offset(i as isize);
                let name = CStr::from_ptr(ibv_get_device_name(d)).to_string_lossy();
                if name == device {
                    dev_ptr = d;
                    break;
                }
            }
            if dev_ptr.is_null() {
                return Err(anyhow!("device {} not found", device));
            }
            let ctx = NonNull::new(ibv_open_device(dev_ptr))
                .ok_or_else(|| anyhow!("ibv_open_device failed"))?;
            resources.ctx = Some(ctx);
            drop(devices);

            // ===== 2. PD + CQ =====
            let pd = NonNull::new(ibv_alloc_pd(ctx.as_ptr()))
                .ok_or_else(|| anyhow!("ibv_alloc_pd failed"))?;
            resources.pd = Some(pd);
            let cq = NonNull::new(ibv_create_cq(
                ctx.as_ptr(),
                128,
                ptr::null_mut(),
                ptr::null_mut(),
                0,
            ))
            .ok_or_else(|| anyhow!("ibv_create_cq failed"))?;
            resources.cq = Some(cq);

            // ===== 3. Query local GID =====
            let mut local_gid: ibv_gid = std::mem::zeroed();
            let rc = ibv_query_gid(ctx.as_ptr(), port, gid_index as i32, &mut local_gid);
            if rc != 0 {
                return Err(anyhow!("ibv_query_gid failed: {}", rc));
            }

            // ===== 4. Allocate + register buffer =====
            let layout = std::alloc::Layout::from_size_align(buf_size, 4096)
                .map_err(|e| anyhow!("bad layout: {}", e))?;
            let buf_ptr = std::alloc::alloc_zeroed(layout);
            if buf_ptr.is_null() {
                return Err(anyhow!("alloc {} bytes failed", buf_size));
            }
            resources.buffer = Some((buf_ptr, layout));
            let mr = NonNull::new(ibv_reg_mr(
                pd.as_ptr(),
                buf_ptr as *mut c_void,
                buf_size,
                (ibv_access_flags::IBV_ACCESS_LOCAL_WRITE.0
                    | ibv_access_flags::IBV_ACCESS_REMOTE_WRITE.0
                    | ibv_access_flags::IBV_ACCESS_REMOTE_READ.0) as i32,
            ))
            .ok_or_else(|| anyhow!("ibv_reg_mr failed: {}", std::io::Error::last_os_error()))?;
            resources.mr = Some(mr);
            let local_rkey = (*mr.as_ptr()).rkey;

            let client = Client {
                port_num: port,
                gid_index,
                control_timeout: DEFAULT_CONTROL_TIMEOUT,
                ctx,
                pd,
                cq,
                qp: None,
                mr,
                buf_ptr,
                buf_size,
                buf_layout: layout,
                local_gid,
                local_qpn: 0,
                local_psn: 0,
                local_rkey,
                stream: None,
                external_regions: Vec::new(),
            };
            std::mem::forget(resources);
            Ok(client)
        }
    }

    fn retire_connection(&mut self) {
        if let Some(stream) = self.stream.take() {
            let _ = stream.shutdown(Shutdown::Both);
        }
        if let Some(qp) = self.qp.take() {
            require_cleanup_success(
                unsafe { ibv_destroy_qp(qp.as_ptr()) },
                b"ContextStore FFI QP destruction failed; aborting\n",
            );
            // A later reconnect reuses this CQ. Consume old/flush completions
            // after QP destruction so they cannot acknowledge a new PUT WR.
            loop {
                let mut completion: ibv_wc = unsafe { std::mem::zeroed() };
                let count = unsafe { ibv_poll_cq(self.cq.as_ptr(), 1, &mut completion) };
                if count < 0 {
                    require_cleanup_success(-1, b"ContextStore FFI CQ drain failed; aborting\n");
                }
                if count == 0 {
                    break;
                }
            }
        }
    }

    fn settle_on_error<T>(&mut self, operation: impl FnOnce(&mut Self) -> Result<T>) -> Result<T> {
        let result = operation(self);
        if result.is_err() {
            self.retire_connection();
        }
        result
    }

    fn set_timeout(&mut self, timeout_ms: u64) -> Result<()> {
        if timeout_ms == 0 {
            return Err(anyhow!("control timeout must be positive"));
        }
        let timeout = Duration::from_millis(timeout_ms);
        if let Some(stream) = self.stream.as_mut() {
            stream.set_read_timeout(Some(timeout))?;
            stream.set_write_timeout(Some(timeout))?;
        }
        self.control_timeout = timeout;
        Ok(())
    }

    fn connect(&mut self, server_addr: &str) -> Result<()> {
        self.retire_connection();
        unsafe {
            // ===== 1. Create QP =====
            let mut qp_attr = ibv_qp_init_attr {
                qp_context: ptr::null_mut(),
                send_cq: self.cq.as_ptr(),
                recv_cq: self.cq.as_ptr(),
                srq: ptr::null_mut(),
                cap: ibv_qp_cap {
                    max_send_wr: 128,
                    max_recv_wr: 128,
                    max_send_sge: 4,
                    max_recv_sge: 4,
                    max_inline_data: 0,
                },
                qp_type: ibv_qp_type::IBV_QPT_RC,
                sq_sig_all: 0,
            };
            let qp = NonNull::new(ibv_create_qp(self.pd.as_ptr(), &mut qp_attr))
                .ok_or_else(|| anyhow!("ibv_create_qp failed"))?;
            self.local_qpn = (*qp.as_ptr()).qp_num;
            self.local_psn = (std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_nanos() as u32)
                .unwrap_or(0))
                & 0xFFFFFF;
            self.qp = Some(qp);

            // ===== 2. to_init =====
            let mut attr: ibv_qp_attr = std::mem::zeroed();
            attr.qp_state = ibv_qp_state::IBV_QPS_INIT;
            attr.pkey_index = 0;
            attr.port_num = self.port_num;
            attr.qp_access_flags = (ibv_access_flags::IBV_ACCESS_LOCAL_WRITE.0
                | ibv_access_flags::IBV_ACCESS_REMOTE_WRITE.0
                | ibv_access_flags::IBV_ACCESS_REMOTE_READ.0)
                as i32 as u32;
            let mask = ibv_qp_attr_mask::IBV_QP_STATE
                | ibv_qp_attr_mask::IBV_QP_PKEY_INDEX
                | ibv_qp_attr_mask::IBV_QP_PORT
                | ibv_qp_attr_mask::IBV_QP_ACCESS_FLAGS;
            let rc = ibv_modify_qp(qp.as_ptr(), &mut attr, mask.0 as i32);
            if rc != 0 {
                return Err(anyhow!("modify_qp INIT: {}", rc));
            }

            // ===== 3. TCP connect + exchange QP info =====
            let started = Instant::now();
            let mut connected = None;
            let mut last_error = None;
            for address in server_addr.to_socket_addrs()? {
                let remaining = self.control_timeout.saturating_sub(started.elapsed());
                if remaining.is_zero() {
                    break;
                }
                match TcpStream::connect_timeout(&address, remaining) {
                    Ok(stream) => {
                        connected = Some(stream);
                        break;
                    }
                    Err(error) => last_error = Some(error),
                }
            }
            let mut stream = connected
                .ok_or_else(|| anyhow!("tcp connect {}: {:?}", server_addr, last_error))?;
            stream.set_nodelay(true)?;
            stream.set_read_timeout(Some(self.control_timeout))?;
            stream.set_write_timeout(Some(self.control_timeout))?;
            let local_qp_info = QpInfo {
                qpn: self.local_qpn,
                psn: self.local_psn,
                gid: self.local_gid,
            };
            let mut hello = Vec::with_capacity(25);
            hello.push(MSG_HELLO);
            hello.extend_from_slice(&local_qp_info.to_bytes());
            stream.write_all(&hello)?;
            stream.flush()?;
            // recv server hello
            let mut tag = [0u8; 1];
            stream.read_exact(&mut tag)?;
            if tag[0] != MSG_HELLO {
                return Err(anyhow!("expected HELLO, got {}", tag[0]));
            }
            let mut body = [0u8; 24];
            stream.read_exact(&mut body)?;
            let remote = QpInfo::from_bytes(&body);

            // ===== 4. to_rtr + to_rts =====
            let mut attr: ibv_qp_attr = std::mem::zeroed();
            attr.qp_state = ibv_qp_state::IBV_QPS_RTR;
            attr.path_mtu = ibv_mtu::IBV_MTU_1024;
            attr.dest_qp_num = remote.qpn;
            attr.rq_psn = remote.psn;
            attr.max_dest_rd_atomic = 1;
            attr.min_rnr_timer = 12;
            attr.ah_attr.is_global = 1;
            attr.ah_attr.dlid = 0;
            attr.ah_attr.sl = 0;
            attr.ah_attr.src_path_bits = 0;
            attr.ah_attr.port_num = self.port_num;
            attr.ah_attr.grh.dgid = remote.gid;
            attr.ah_attr.grh.flow_label = 0;
            attr.ah_attr.grh.hop_limit = 1;
            attr.ah_attr.grh.sgid_index = self.gid_index;
            attr.ah_attr.grh.traffic_class = 0;
            let mask = ibv_qp_attr_mask::IBV_QP_STATE
                | ibv_qp_attr_mask::IBV_QP_AV
                | ibv_qp_attr_mask::IBV_QP_PATH_MTU
                | ibv_qp_attr_mask::IBV_QP_DEST_QPN
                | ibv_qp_attr_mask::IBV_QP_RQ_PSN
                | ibv_qp_attr_mask::IBV_QP_MAX_DEST_RD_ATOMIC
                | ibv_qp_attr_mask::IBV_QP_MIN_RNR_TIMER;
            let rc = ibv_modify_qp(qp.as_ptr(), &mut attr, mask.0 as i32);
            if rc != 0 {
                return Err(anyhow!(
                    "modify_qp RTR: {} errno={}",
                    rc,
                    std::io::Error::last_os_error()
                ));
            }
            let mut attr: ibv_qp_attr = std::mem::zeroed();
            attr.qp_state = ibv_qp_state::IBV_QPS_RTS;
            attr.timeout = 14;
            attr.retry_cnt = 7;
            attr.rnr_retry = 7;
            attr.sq_psn = self.local_psn;
            attr.max_rd_atomic = 1;
            let mask = ibv_qp_attr_mask::IBV_QP_STATE
                | ibv_qp_attr_mask::IBV_QP_TIMEOUT
                | ibv_qp_attr_mask::IBV_QP_RETRY_CNT
                | ibv_qp_attr_mask::IBV_QP_RNR_RETRY
                | ibv_qp_attr_mask::IBV_QP_SQ_PSN
                | ibv_qp_attr_mask::IBV_QP_MAX_QP_RD_ATOMIC;
            let rc = ibv_modify_qp(qp.as_ptr(), &mut attr, mask.0 as i32);
            if rc != 0 {
                return Err(anyhow!("modify_qp RTS: {}", rc));
            }

            self.stream = Some(stream);
            Ok(())
        }
    }

    /// Send a GET request; wait for the server WRITE to complete; return bytes written
    /// (0 = miss).
    fn get(&mut self, key: &str) -> Result<u64> {
        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("not connected"))?;
        let local_addr = self.buf_ptr as u64;

        // send GetReq
        let key_bytes = key.as_bytes();
        if key_bytes.len() > u16::MAX as usize {
            return Err(anyhow!("key exceeds wire length limit"));
        }
        let mut req = Vec::with_capacity(1 + 2 + key_bytes.len() + 8 + 4 + 8);
        req.push(MSG_GET_REQ);
        req.extend_from_slice(&(key_bytes.len() as u16).to_le_bytes());
        req.extend_from_slice(key_bytes);
        req.extend_from_slice(&local_addr.to_le_bytes());
        req.extend_from_slice(&self.local_rkey.to_le_bytes());
        req.extend_from_slice(&(self.buf_size as u64).to_le_bytes());
        stream.write_all(&req)?;
        stream.flush()?;

        // recv GetResp: tag(1) + found(1) + bytes(8) + nchunks(4)
        read_get_response(stream, self.buf_size as u64, None)
    }

    /// Register a caller-owned buffer as an RDMA MR. Returns region_id (Vec index).
    ///
    /// # Safety
    /// The caller must guarantee:
    /// - `ptr` points to valid + alive memory, lasting at least until unregister or
    ///   client drop
    /// - Memory should be page-aligned (4KB) to avoid some NIC firmware rejections
    /// - Strongly recommended to be pinned (mlock / cudaHostAlloc); otherwise reg_mr
    ///   will trigger an implicit pin at high cost
    unsafe fn register_external(&mut self, ptr: *const u8, size: usize) -> Result<u32> {
        if ptr.is_null() || size == 0 {
            return Err(anyhow!("register_external: null ptr or zero size"));
        }
        let mr = NonNull::new(ibv_reg_mr(
            self.pd.as_ptr(),
            ptr as *mut c_void,
            size,
            (ibv_access_flags::IBV_ACCESS_LOCAL_WRITE.0
                | ibv_access_flags::IBV_ACCESS_REMOTE_WRITE.0
                | ibv_access_flags::IBV_ACCESS_REMOTE_READ.0) as i32,
        ))
        .ok_or_else(|| {
            anyhow!(
                "ibv_reg_mr (external {} bytes) failed: {}",
                size,
                std::io::Error::last_os_error()
            )
        })?;
        let region = ExternalRegion {
            mr,
            ptr,
            size,
            lkey: (*mr.as_ptr()).lkey,
            rkey: (*mr.as_ptr()).rkey,
        };
        // Prefer to reuse a hole (left by unregister); otherwise push a new slot.
        for (i, slot) in self.external_regions.iter_mut().enumerate() {
            if slot.is_none() {
                *slot = Some(region);
                return Ok(i as u32);
            }
        }
        self.external_regions.push(Some(region));
        Ok((self.external_regions.len() - 1) as u32)
    }

    /// Send GET; the server WRITEs to `external_regions[region_id].ptr + offset`.
    /// Returns bytes written (0 = miss).
    fn get_into(&mut self, region_id: u32, key: &str, offset: u64) -> Result<u64> {
        let region = self
            .external_regions
            .get(region_id as usize)
            .and_then(|opt| opt.as_ref())
            .ok_or_else(|| anyhow!("get_into: invalid region_id {}", region_id))?;

        let region_ptr = region.ptr;
        let region_size = region.size;
        let region_rkey = region.rkey;
        if offset >= region_size as u64 {
            return Err(anyhow!(
                "get_into: offset {} >= region size {}",
                offset,
                region_size
            ));
        }
        let available = region_size - offset as usize;

        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("not connected"))?;
        // Use the offset within the region as the destination address.
        let dst_addr = (region_ptr as u64)
            .checked_add(offset)
            .ok_or_else(|| anyhow!("external target address overflow"))?;

        // send GetReq: max_size uses the remaining region capacity so the server can
        // clamp as needed.
        let key_bytes = key.as_bytes();
        if key_bytes.len() > u16::MAX as usize {
            return Err(anyhow!("key exceeds wire length limit"));
        }
        let mut req = Vec::with_capacity(1 + 2 + key_bytes.len() + 8 + 4 + 8);
        req.push(MSG_GET_REQ);
        req.extend_from_slice(&(key_bytes.len() as u16).to_le_bytes());
        req.extend_from_slice(key_bytes);
        req.extend_from_slice(&dst_addr.to_le_bytes());
        req.extend_from_slice(&region_rkey.to_le_bytes());
        req.extend_from_slice(&(available as u64).to_le_bytes());
        stream.write_all(&req)?;
        stream.flush()?;

        // recv GetResp
        read_get_response(stream, available as u64, None)
    }

    /// Descriptor GET: server reads the specified version according to the
    /// client-cached object descriptor.
    #[allow(clippy::too_many_arguments)]
    fn get_descriptor_into(
        &mut self,
        region_id: u32,
        key: &str,
        object_handle: &str,
        object_generation: u64,
        content_etag: &str,
        layout_version: u64,
        size: u64,
        is_striped: bool,
        stripe_count: u32,
        chunk_size: u64,
        offset: u64,
    ) -> Result<u64> {
        let region = self
            .external_regions
            .get(region_id as usize)
            .and_then(|opt| opt.as_ref())
            .ok_or_else(|| anyhow!("get_descriptor_into: invalid region_id {}", region_id))?;

        let region_ptr = region.ptr;
        let region_size = region.size;
        let region_rkey = region.rkey;
        if offset >= region_size as u64 {
            return Err(anyhow!(
                "get_descriptor_into: offset {} >= region size {}",
                offset,
                region_size
            ));
        }
        let available = region_size - offset as usize;
        if size > available as u64 {
            return Err(anyhow!(
                "descriptor object exceeds external target capacity"
            ));
        }
        let dst_addr = (region_ptr as u64)
            .checked_add(offset)
            .ok_or_else(|| anyhow!("external target address overflow"))?;

        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("not connected"))?;

        fn push_string(frame: &mut Vec<u8>, value: &str, field: &str) -> Result<()> {
            let bytes = value.as_bytes();
            if bytes.len() > u16::MAX as usize {
                return Err(anyhow!("{} too long: {}", field, bytes.len()));
            }
            frame.extend_from_slice(&(bytes.len() as u16).to_le_bytes());
            frame.extend_from_slice(bytes);
            Ok(())
        }

        let mut req = Vec::with_capacity(
            1 + 2
                + key.len()
                + 2
                + object_handle.len()
                + 8
                + 2
                + content_etag.len()
                + 8
                + 8
                + 1
                + 4
                + 8
                + 8
                + 4
                + 8,
        );
        req.push(MSG_GET_DESCRIPTOR_REQ);
        push_string(&mut req, key, "key")?;
        push_string(&mut req, object_handle, "object_handle")?;
        req.extend_from_slice(&object_generation.to_le_bytes());
        push_string(&mut req, content_etag, "content_etag")?;
        req.extend_from_slice(&layout_version.to_le_bytes());
        req.extend_from_slice(&size.to_le_bytes());
        req.push(if is_striped { 1 } else { 0 });
        req.extend_from_slice(&stripe_count.to_le_bytes());
        req.extend_from_slice(&chunk_size.to_le_bytes());
        req.extend_from_slice(&dst_addr.to_le_bytes());
        req.extend_from_slice(&region_rkey.to_le_bytes());
        req.extend_from_slice(&(available as u64).to_le_bytes());
        stream.write_all(&req)?;
        stream.flush()?;

        read_get_response(stream, available as u64, Some(size))
    }

    #[allow(clippy::too_many_arguments)]
    fn get_descriptor(
        &mut self,
        key: &str,
        object_handle: &str,
        object_generation: u64,
        content_etag: &str,
        layout_version: u64,
        size: u64,
        is_striped: bool,
        stripe_count: u32,
        chunk_size: u64,
    ) -> Result<u64> {
        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("not connected"))?;

        fn push_string(frame: &mut Vec<u8>, value: &str, field: &str) -> Result<()> {
            let bytes = value.as_bytes();
            if bytes.len() > u16::MAX as usize {
                return Err(anyhow!("{} too long: {}", field, bytes.len()));
            }
            frame.extend_from_slice(&(bytes.len() as u16).to_le_bytes());
            frame.extend_from_slice(bytes);
            Ok(())
        }

        if size > self.buf_size as u64 {
            return Err(anyhow!(
                "descriptor object exceeds built-in target capacity"
            ));
        }
        let local_addr = self.buf_ptr as u64;
        let mut req = Vec::with_capacity(
            1 + 2
                + key.len()
                + 2
                + object_handle.len()
                + 8
                + 2
                + content_etag.len()
                + 8
                + 8
                + 1
                + 4
                + 8
                + 8
                + 4
                + 8,
        );
        req.push(MSG_GET_DESCRIPTOR_REQ);
        push_string(&mut req, key, "key")?;
        push_string(&mut req, object_handle, "object_handle")?;
        req.extend_from_slice(&object_generation.to_le_bytes());
        push_string(&mut req, content_etag, "content_etag")?;
        req.extend_from_slice(&layout_version.to_le_bytes());
        req.extend_from_slice(&size.to_le_bytes());
        req.push(if is_striped { 1 } else { 0 });
        req.extend_from_slice(&stripe_count.to_le_bytes());
        req.extend_from_slice(&chunk_size.to_le_bytes());
        req.extend_from_slice(&local_addr.to_le_bytes());
        req.extend_from_slice(&self.local_rkey.to_le_bytes());
        req.extend_from_slice(&(self.buf_size as u64).to_le_bytes());
        stream.write_all(&req)?;
        stream.flush()?;

        read_get_response(stream, self.buf_size as u64, Some(size))
    }

    /// Unregister an external buffer (dereg_mr). Caller is responsible for freeing
    /// their own memory.
    fn unregister_external(&mut self, region_id: u32) -> Result<()> {
        let idx = region_id as usize;
        let slot = self
            .external_regions
            .get_mut(idx)
            .ok_or_else(|| anyhow!("unregister: invalid region_id {}", region_id))?;
        match slot.take() {
            Some(region) => {
                unsafe {
                    require_cleanup_success(
                        ibv_dereg_mr(region.mr.as_ptr()),
                        b"ContextStore FFI external MR deregistration failed; aborting\n",
                    );
                }
                Ok(())
            }
            None => Err(anyhow!(
                "unregister: region_id {} already unregistered",
                region_id
            )),
        }
    }

    /// **PUT data plane**: RDMA-WRITE `external_regions[region_id].ptr[offset..offset+size]`
    /// to the server (which temporarily allocates a slab extent as the destination),
    /// then wait for the server-side flush to complete. Returns Ok(()) on success,
    /// Err on failure.
    ///
    /// Flow:
    /// 1. send PUT_REQ {key, size}
    /// 2. recv PUT_READY {ok, dst_addr, dst_rkey} — if ok=false, return Err immediately
    /// 3. post_send(RDMA_WRITE, src=ext_region+offset, dst=dst_addr/dst_rkey, signaled=true)
    /// 4. poll CQ waiting for WRITE completion
    /// 5. send PUT_COMMIT
    /// 6. recv PUT_RESP {ok}
    fn put(&mut self, region_id: u32, key: &str, offset: u64, size: u64) -> Result<()> {
        let region = self
            .external_regions
            .get(region_id as usize)
            .and_then(|opt| opt.as_ref())
            .ok_or_else(|| anyhow!("put: invalid region_id {}", region_id))?;
        let end = offset
            .checked_add(size)
            .ok_or_else(|| anyhow!("PUT range overflow"))?;
        if size == 0 || end > region.size as u64 {
            return Err(anyhow!(
                "put: offset {} + size {} > region size {}",
                offset,
                size,
                region.size
            ));
        }
        let src_addr = (region.ptr as u64)
            .checked_add(offset)
            .ok_or_else(|| anyhow!("external source address overflow"))?;
        let src_lkey = region.lkey;

        let qp = self.qp.ok_or_else(|| anyhow!("put: QP not initialized"))?;
        let cq = self.cq;

        let stream = self
            .stream
            .as_mut()
            .ok_or_else(|| anyhow!("not connected"))?;

        // ===== 1. send PUT_REQ =====
        let key_bytes = key.as_bytes();
        if key_bytes.len() > u16::MAX as usize {
            return Err(anyhow!("key exceeds wire length limit"));
        }
        let mut req = Vec::with_capacity(1 + 2 + key_bytes.len() + 8);
        req.push(MSG_PUT_REQ);
        req.extend_from_slice(&(key_bytes.len() as u16).to_le_bytes());
        req.extend_from_slice(key_bytes);
        req.extend_from_slice(&size.to_le_bytes());
        stream.write_all(&req)?;
        stream.flush()?;

        // ===== 2. recv PUT_READY {ok, dst_addr, dst_rkey} =====
        let mut tag = [0u8; 1];
        stream.read_exact(&mut tag)?;
        if tag[0] != MSG_PUT_READY {
            return Err(anyhow!(
                "expected PUT_READY={}, got {}",
                MSG_PUT_READY,
                tag[0]
            ));
        }
        let mut body = [0u8; 1 + 8 + 4];
        stream.read_exact(&mut body)?;
        let ok = body[0] != 0;
        let dst_addr = u64::from_le_bytes([
            body[1], body[2], body[3], body[4], body[5], body[6], body[7], body[8],
        ]);
        let dst_rkey = u32::from_le_bytes([body[9], body[10], body[11], body[12]]);
        if !ok {
            // Server rejected (slab full / no slab). Don't send COMMIT; just recv
            // PUT_RESP{ok=false}.
            let mut tag2 = [0u8; 1];
            stream.read_exact(&mut tag2)?;
            let mut body2 = [0u8; 1];
            stream.read_exact(&mut body2)?;
            return Err(anyhow!("server PUT_READY rejected"));
        }

        // ===== 3. Post RDMA WRITEs to push data to the server slab =====
        // A single WRITE has ibv_sge.length as u32 → max 4GB. Actual sizes stay under
        // 1-2GB (single-layer KV). If size > u32::MAX, split (rare path).
        const MAX_WR_LEN: u64 = 1 << 30; // 1GB per WRITE
        let n_writes = size.div_ceil(MAX_WR_LEN);
        let mut off = 0u64;
        let mut wr_idx = 0u64;
        unsafe {
            while off < size {
                let chunk = (size - off).min(MAX_WR_LEN);
                let signaled = wr_idx + 1 == n_writes; // Only signal the last WR.
                let mut sge = ibv_sge {
                    addr: src_addr + off,
                    length: chunk as u32,
                    lkey: src_lkey,
                };
                let mut wr: ibv_send_wr = std::mem::zeroed();
                wr.wr_id = wr_idx;
                wr.sg_list = &mut sge;
                wr.num_sge = 1;
                wr.opcode = ibv_wr_opcode::IBV_WR_RDMA_WRITE;
                wr.send_flags = if signaled {
                    ibv_send_flags::IBV_SEND_SIGNALED.0
                } else {
                    0
                };
                wr.wr.rdma.remote_addr = dst_addr + off;
                wr.wr.rdma.rkey = dst_rkey;
                let mut bad_wr: *mut ibv_send_wr = ptr::null_mut();
                let rc = ibv_post_send(qp.as_ptr(), &mut wr, &mut bad_wr);
                if rc != 0 {
                    return Err(anyhow!("ibv_post_send (PUT WRITE) rc={}", rc));
                }
                off += chunk;
                wr_idx += 1;
            }

            // ===== 4. Poll for completion (busy poll) =====
            let mut wc: ibv_wc = std::mem::zeroed();
            let start = std::time::Instant::now();
            loop {
                let n = ibv_poll_cq(cq.as_ptr(), 1, &mut wc);
                if n < 0 {
                    return Err(anyhow!("ibv_poll_cq error"));
                }
                if n > 0 {
                    if wc.status != ibv_wc_status::IBV_WC_SUCCESS {
                        return Err(anyhow!(
                            "RDMA WRITE wr_id={} status={}",
                            wc.wr_id,
                            wc.status
                        ));
                    }
                    break;
                }
                if start.elapsed().as_secs() > 30 {
                    return Err(anyhow!("RDMA WRITE poll_cq timeout 30s"));
                }
            }
        }

        // ===== 5. send PUT_COMMIT (1 byte tag) =====
        stream.write_all(&[MSG_PUT_COMMIT])?;
        stream.flush()?;

        // ===== 6. recv PUT_RESP {ok} =====
        let mut tag = [0u8; 1];
        stream.read_exact(&mut tag)?;
        if tag[0] != MSG_PUT_RESP {
            return Err(anyhow!(
                "expected PUT_RESP={}, got {}",
                MSG_PUT_RESP,
                tag[0]
            ));
        }
        let mut body = [0u8; 1];
        stream.read_exact(&mut body)?;
        if body[0] == 0 {
            return Err(anyhow!("server PUT_RESP ok=false (pwrite failed)"));
        }
        Ok(())
    }

    fn close(&mut self) {
        if let Some(mut s) = self.stream.take() {
            let _ = s.write_all(&[MSG_BYE]);
            let _ = s.flush();
        }
        self.retire_connection();
    }
}

impl Drop for Client {
    fn drop(&mut self) {
        self.close();
        unsafe {
            // First dereg all external MRs (same PD as self.mr; must precede dealloc_pd).
            for region in self.external_regions.drain(..).flatten() {
                require_cleanup_success(
                    ibv_dereg_mr(region.mr.as_ptr()),
                    b"ContextStore FFI external MR deregistration failed; aborting\n",
                );
            }
            require_cleanup_success(
                ibv_dereg_mr(self.mr.as_ptr()),
                b"ContextStore FFI MR deregistration failed; aborting\n",
            );
            std::alloc::dealloc(self.buf_ptr, self.buf_layout);
            require_cleanup_success(
                ibv_destroy_cq(self.cq.as_ptr()),
                b"ContextStore FFI CQ destruction failed; aborting\n",
            );
            require_cleanup_success(
                ibv_dealloc_pd(self.pd.as_ptr()),
                b"ContextStore FFI PD deallocation failed; aborting\n",
            );
            require_cleanup_success(
                ibv_close_device(self.ctx.as_ptr()),
                b"ContextStore FFI device close failed; aborting\n",
            );
        }
    }
}

// ====================================================================
// C ABI Exports
// ====================================================================

/// Lifecycle ABI version: failed transfers retire their QP before returning,
/// and uncertain provider cleanup stops the process before memory is reused.
#[no_mangle]
pub extern "C" fn cs_rdma_client_lifecycle_version() -> u32 {
    1
}

/// Create an RDMA client. Returns NULL on failure.
///
/// # Safety
/// - `device` must be a valid C string
/// - The returned handle must be released with `cs_rdma_client_free`
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_new(
    device: *const c_char,
    port: u8,
    gid_index: u8,
    buf_size: u64,
) -> *mut c_void {
    if device.is_null() {
        return ptr::null_mut();
    }
    let dev = match CStr::from_ptr(device).to_str() {
        Ok(s) => s,
        Err(_) => return ptr::null_mut(),
    };
    match Client::new(dev, port, gid_index, buf_size as usize) {
        Ok(c) => Box::into_raw(Box::new(c)) as *mut c_void,
        Err(e) => {
            report_error("new", &e);
            ptr::null_mut()
        }
    }
}

/// TCP connect to the server + set up QP. Returns non-zero on failure.
///
/// # Safety
/// `client` must be live and exclusively accessed. `server_addr` must be a valid C string.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_connect(
    client: *mut c_void,
    server_addr: *const c_char,
) -> c_int {
    if client.is_null() || server_addr.is_null() {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    let addr = match CStr::from_ptr(server_addr).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    match c.settle_on_error(|client| client.connect(addr)) {
        Ok(()) => 0,
        Err(e) => {
            report_error("connect", &e);
            -3
        }
    }
}

/// Set a positive TCP control deadline after DNS resolution. Zero is rejected.
///
/// # Safety
/// `client` must be a live handle and no other operation may use it concurrently.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_set_timeout_ms(
    client: *mut c_void,
    timeout_ms: u64,
) -> i32 {
    if client.is_null() || timeout_ms == 0 {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    match c.settle_on_error(|client| client.set_timeout(timeout_ms)) {
        Ok(()) => 0,
        Err(error) => {
            report_error("set_timeout", &error);
            -2
        }
    }
}

/// Send GET; returns bytes written; 0 = miss; <0 = error.
///
/// # Safety
/// `client` must be live and exclusively accessed. `key` must be a valid C string.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_get(client: *mut c_void, key: *const c_char) -> i64 {
    if client.is_null() || key.is_null() {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    let k = match CStr::from_ptr(key).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    match c.settle_on_error(|client| client.get(k)) {
        Ok(n) => n as i64,
        Err(e) => {
            report_error("get", &e);
            -3
        }
    }
}

/// Get the buffer pointer. Python can do ctypes.string_at(ptr, n) for a zero-copy view.
///
/// # Safety
/// `client` must be live and exclusively accessed. The returned pointer must not outlive the handle or be accessed during transfers.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_buffer(client: *mut c_void) -> *const u8 {
    if client.is_null() {
        return ptr::null();
    }
    let c = &*(client as *const Client);
    c.buf_ptr as *const u8
}

///
/// # Safety
/// `client` must be live and exclusively accessed.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_buffer_size(client: *mut c_void) -> u64 {
    if client.is_null() {
        return 0;
    }
    let c = &*(client as *const Client);
    c.buf_size as u64
}

/// Free the client (implicitly close + dealloc).
///
/// # Safety
/// `client` must be a live handle freed exactly once, without concurrent operations. External buffers must remain alive until this call returns.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_free(client: *mut c_void) {
    if !client.is_null() {
        let _ = Box::from_raw(client as *mut Client);
    }
}

// ====================================================================
// Plan A: zero-copy external buffer interface
// ====================================================================

/// Register a caller-owned pinned host buffer as an RDMA MR.
///
/// Returns region_id (>=0); returns -1 on failure.
///
/// # Safety
/// - `ptr` must point to valid + alive host memory, lasting at least until the
///   matching unregister or client free
/// - 4KB page alignment is recommended (`posix_memalign` / `cudaHostAlloc`)
/// - Memory should already be pinned (mlock / cudaHostAlloc); otherwise reg_mr will
///   trigger an implicit pin (slow, and subject to RLIMIT_MEMLOCK)
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_register_external_buffer(
    client: *mut c_void,
    ptr: *const u8,
    size: u64,
) -> i32 {
    if client.is_null() {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    match c.register_external(ptr, size as usize) {
        Ok(id) => id as i32,
        Err(e) => {
            report_error("register_external", &e);
            -1
        }
    }
}

/// Send GET; server WRITEs into `region_id`'s external buffer at `offset`.
/// Returns bytes_written; 0 = miss; <0 = error.
///
/// # Safety
/// `client` must be live and exclusively accessed. `key` must be a valid C string; the registered target must remain writable and alive through this call.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_get_into(
    client: *mut c_void,
    region_id: i32,
    key: *const c_char,
    offset: u64,
) -> i64 {
    if client.is_null() || key.is_null() || region_id < 0 {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    let k = match CStr::from_ptr(key).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    match c.settle_on_error(|client| client.get_into(region_id as u32, k, offset)) {
        Ok(n) => n as i64,
        Err(e) => {
            report_error("get_into", &e);
            -3
        }
    }
}

/// Descriptor GET: server RDMA-WRITEs the version pointed to by the descriptor into
/// the external buffer.
///
/// # Safety
/// The live handle requires exclusive access; all string pointers must be valid C strings. The registered target must remain writable and alive through this call.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn cs_rdma_client_get_descriptor_into(
    client: *mut c_void,
    region_id: i32,
    key: *const c_char,
    object_handle: *const c_char,
    object_generation: u64,
    content_etag: *const c_char,
    layout_version: u64,
    size: u64,
    is_striped: u8,
    stripe_count: u32,
    chunk_size: u64,
    offset: u64,
) -> i64 {
    if client.is_null()
        || key.is_null()
        || object_handle.is_null()
        || content_etag.is_null()
        || region_id < 0
    {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    let k = match CStr::from_ptr(key).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    let handle = match CStr::from_ptr(object_handle).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    let etag = match CStr::from_ptr(content_etag).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    match c.settle_on_error(|client| {
        client.get_descriptor_into(
            region_id as u32,
            k,
            handle,
            object_generation,
            etag,
            layout_version,
            size,
            is_striped != 0,
            stripe_count,
            chunk_size,
            offset,
        )
    }) {
        Ok(n) => n as i64,
        Err(e) => {
            report_error("get_descriptor_into", &e);
            -3
        }
    }
}

/// Descriptor GET into the client's built-in buffer.
///
/// # Safety
/// The live handle requires exclusive access; all string pointers must be valid C strings.
#[no_mangle]
#[allow(clippy::too_many_arguments)]
pub unsafe extern "C" fn cs_rdma_client_get_descriptor(
    client: *mut c_void,
    key: *const c_char,
    object_handle: *const c_char,
    object_generation: u64,
    content_etag: *const c_char,
    layout_version: u64,
    size: u64,
    is_striped: u8,
    stripe_count: u32,
    chunk_size: u64,
) -> i64 {
    if client.is_null() || key.is_null() || object_handle.is_null() || content_etag.is_null() {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    let k = match CStr::from_ptr(key).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    let handle = match CStr::from_ptr(object_handle).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    let etag = match CStr::from_ptr(content_etag).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    match c.settle_on_error(|client| {
        client.get_descriptor(
            k,
            handle,
            object_generation,
            etag,
            layout_version,
            size,
            is_striped != 0,
            stripe_count,
            chunk_size,
        )
    }) {
        Ok(n) => n as i64,
        Err(e) => {
            report_error("get_descriptor", &e);
            -3
        }
    }
}

/// Unregister an external buffer (dereg_mr). Caller is responsible for freeing their
/// own memory. Returns 0 on success, <0 on error.
///
/// # Safety
/// The live handle requires exclusive access. The external allocation must remain alive until successful return.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_unregister_external_buffer(
    client: *mut c_void,
    region_id: i32,
) -> i32 {
    if client.is_null() || region_id < 0 {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    match c.unregister_external(region_id as u32) {
        Ok(()) => 0,
        Err(e) => {
            report_error("unregister_external", &e);
            -2
        }
    }
}

/// **PUT data plane**: RDMA-WRITE `external_regions[region_id].ptr[offset..offset+size]`
/// to the server; the server flushes it and returns. Returns 0 on success, <0 on error.
///
/// Usage (vLLM connector PUT hot path):
/// - The connector's pinned host buffer is registered once via `register_external_buffer`
/// - On write, the connector places data at some offset in the buffer, then calls this
///   interface to push it to the server
/// - The server does a zero-memcpy O_DIRECT flush to NVMe (8 stripes in parallel)
///
/// Fully symmetric with the GET path: GET is the server WRITEing into our buffer;
/// PUT is us WRITEing into the server's slab.
///
/// # Safety
/// The live handle requires exclusive access; `key` must be a valid C string. The registered source must remain alive and unmodified through this call.
#[no_mangle]
pub unsafe extern "C" fn cs_rdma_client_put(
    client: *mut c_void,
    region_id: i32,
    key: *const c_char,
    offset: u64,
    size: u64,
) -> i32 {
    if client.is_null() || key.is_null() || region_id < 0 {
        return -1;
    }
    let c = &mut *(client as *mut Client);
    let k = match CStr::from_ptr(key).to_str() {
        Ok(s) => s,
        Err(_) => return -2,
    };
    match c.settle_on_error(|client| client.put(region_id as u32, k, offset, size)) {
        Ok(()) => 0,
        Err(e) => {
            report_error("put", &e);
            -3
        }
    }
}
