//! Multi-rail descriptor reads for a single client Worker.

use crate::pb;
use crate::rdma::{RdmaClient, RdmaClientConfig, RdmaReadOutcome};
use std::collections::{HashMap, HashSet};
use std::fmt;
use std::path::Path;
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

mod connection_pool;
use connection_pool::{finish_transfer, ConnectionPool, Lease};
pub use connection_pool::{RailConnectionPoolOptions, RailConnectionPoolSnapshot};

/// One local RDMA device and one listener on the node advertised by placement.
#[derive(Clone, Debug)]
pub struct RailRoute {
    /// Stable name used in metrics and errors.
    pub id: String,
    /// Owning node's endpoint returned by `LookupObject`.
    pub advertised_endpoint: String,
    /// Local device and the listener actually dialed over this rail.
    pub connection: RdmaClientConfig,
    /// Initial availability; `RailReader::set_enabled` can change it later.
    pub enabled: bool,
    /// Relative capacity hint used by byte-weighted scheduling.
    pub weight: u32,
}

/// Local RDMA device placement reported by sysfs when available.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct RailTopology {
    /// NUMA node from sysfs, when the host reports one.
    pub numa_node: Option<i32>,
    /// PCI bus/device/function address of the local HCA.
    pub pci_bdf: Option<String>,
}

fn read_topology_from(base: &Path, device: &str) -> RailTopology {
    let path = base.join(device).join("device");
    let numa_node = std::fs::read_to_string(path.join("numa_node"))
        .ok()
        .and_then(|text| text.trim().parse::<i32>().ok())
        .filter(|node| *node >= 0);
    let pci_bdf = std::fs::read_link(&path).ok().and_then(|target| {
        target
            .file_name()
            .map(|name| name.to_string_lossy().into_owned())
            .filter(|name| {
                let bytes = name.as_bytes();
                bytes.len() == 12
                    && bytes[4] == b':'
                    && bytes[7] == b':'
                    && bytes[10] == b'.'
                    && bytes.iter().enumerate().all(|(index, byte)| {
                        matches!(index, 4 | 7 | 10) || byte.is_ascii_hexdigit()
                    })
            })
    });
    RailTopology { numa_node, pci_bdf }
}

impl RailRoute {
    /// Configure one local/remote path for an advertised storage endpoint.
    pub fn new(
        id: impl Into<String>,
        advertised_endpoint: impl Into<String>,
        connection: RdmaClientConfig,
    ) -> Self {
        Self {
            id: id.into(),
            advertised_endpoint: advertised_endpoint.into(),
            connection,
            enabled: true,
            weight: 1,
        }
    }

    /// Keep a configured path visible while excluding it from read plans.
    pub fn disabled(mut self) -> Self {
        self.enabled = false;
        self
    }

    /// Set a positive relative scheduling weight for this path.
    pub fn with_weight(mut self, weight: u32) -> Self {
        self.weight = weight.max(1);
        self
    }
}

/// A local Verbs path whose fabric ID is matched to a server-advertised listener.
/// No remote endpoint is accepted from local configuration in discovery mode.
#[derive(Clone, Debug)]
pub struct LocalRailPath {
    id: String,
    fabric_id: String,
    device: String,
    port: u8,
    gid_index: u8,
    weight: u32,
}

impl LocalRailPath {
    pub fn new(
        id: impl Into<String>,
        fabric_id: impl Into<String>,
        device: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            fabric_id: fabric_id.into(),
            device: device.into(),
            port: 1,
            gid_index: 3,
            weight: 1,
        }
    }

    pub fn with_port(mut self, port: u8) -> Self {
        self.port = port;
        self
    }

    pub fn with_gid_index(mut self, gid_index: u8) -> Self {
        self.gid_index = gid_index;
        self
    }

    pub fn with_weight(mut self, weight: u32) -> Self {
        self.weight = weight.max(1);
        self
    }
}

/// Bounds all allocations and transfers made by one reader instance.
#[derive(Clone, Debug)]
pub struct RailLimits {
    /// Maximum concurrent object reads accepted by this reader.
    pub max_active_reads: usize,
    /// Maximum concurrent transfer tasks using one configured rail.
    pub max_active_reads_per_rail: usize,
    /// Maximum final and per-rail staging allocation reserved at once.
    pub max_staging_bytes: u64,
    /// Maximum total MR lengths reserved across active reads.
    pub max_registered_bytes: u64,
    /// Maximum object payload bytes in flight at once.
    pub max_inflight_bytes: u64,
    /// Maximum aggregate payload bytes in flight on one rail.
    pub max_inflight_bytes_per_rail: u64,
    /// TCP control deadline for connect, send, and reply operations.
    pub io_timeout: Duration,
    /// Delay before a failed rail is eligible for a later request.
    pub rail_cooldown: Duration,
    /// Opt in to contiguous direct-to-private-staging reads when eligible.
    pub direct_staging: bool,
    /// Use the direct mode's contiguous scheduler with the packed receive path.
    pub contiguous_schedule: bool,
}

impl Default for RailLimits {
    fn default() -> Self {
        Self {
            max_active_reads: 8,
            max_active_reads_per_rail: 8,
            max_staging_bytes: 4 * 1024 * 1024 * 1024,
            max_registered_bytes: 4 * 1024 * 1024 * 1024,
            max_inflight_bytes: 4 * 1024 * 1024 * 1024,
            max_inflight_bytes_per_rail: 2 * 1024 * 1024 * 1024,
            io_timeout: Duration::from_secs(30),
            rail_cooldown: Duration::from_secs(10),
            direct_staging: false,
            contiguous_schedule: false,
        }
    }
}

/// Typed failure returned without publishing a partial object.
#[derive(Debug)]
pub enum RailReadError {
    InvalidPlacement(String),
    ResourceExhausted(String),
    BufferTooSmall { need: usize, have: usize },
    Transport(String),
    StaleDescriptor,
    Incomplete { expected: usize, actual: usize },
    Checksum { stripe: u32 },
    VersionChanged,
    Cancelled,
    WorkerPanic,
}

impl fmt::Display for RailReadError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidPlacement(reason) => write!(f, "invalid placement: {reason}"),
            Self::ResourceExhausted(reason) => write!(f, "rail resource limit: {reason}"),
            Self::BufferTooSmall { need, have } => {
                write!(f, "read buffer needs {need} bytes, has {have}")
            }
            Self::Transport(reason) => write!(f, "rail transfer failed: {reason}"),
            Self::StaleDescriptor => write!(f, "object descriptor became stale"),
            Self::Incomplete { expected, actual } => {
                write!(
                    f,
                    "incomplete rail read: expected {expected}, received {actual}"
                )
            }
            Self::Checksum { stripe } => write!(f, "checksum mismatch on stripe {stripe}"),
            Self::VersionChanged => write!(f, "object version changed during rail read"),
            Self::Cancelled => write!(f, "rail read cancelled"),
            Self::WorkerPanic => write!(f, "rail worker panicked"),
        }
    }
}

impl std::error::Error for RailReadError {}

pub(crate) fn same_descriptor_identity(
    left: &pb::ObjectDescriptor,
    right: &pb::ObjectDescriptor,
) -> bool {
    left.key == right.key
        && left.object_handle == right.object_handle
        && left.object_generation == right.object_generation
        && left.content_etag == right.content_etag
        && left.layout_version == right.layout_version
        && left.size == right.size
        && left.is_striped == right.is_striped
        && left.stripe_count == right.stripe_count
        && left.chunk_size == right.chunk_size
}

pub(crate) fn commit_if_unchanged(
    initial: &crate::ObjectLookup,
    current: &crate::ObjectLookup,
    payload: &[u8],
    destination: &mut [u8],
    cancel: Option<&RailCancel>,
) -> Result<usize, RailReadError> {
    if !same_descriptor_identity(&initial.descriptor, &current.descriptor)
        || initial.placement != current.placement
    {
        return Err(RailReadError::VersionChanged);
    }
    let size = usize::try_from(initial.descriptor.size)
        .map_err(|_| RailReadError::InvalidPlacement("object size exceeds address space".into()))?;
    if payload.len() != size {
        return Err(RailReadError::Incomplete {
            expected: size,
            actual: payload.len(),
        });
    }
    if destination.len() < size {
        return Err(RailReadError::BufferTooSmall {
            need: size,
            have: destination.len(),
        });
    }
    if let Some(cancel) = cancel {
        cancel.publish_if_live(|| destination[..size].copy_from_slice(payload))?;
    } else {
        destination[..size].copy_from_slice(payload);
    }
    Ok(size)
}

/// Cooperative request cancellation shared with the caller.
#[derive(Default)]
struct CancelState {
    cancelled: AtomicBool,
    publish_gate: Mutex<()>,
}

#[derive(Clone, Default)]
pub struct RailCancel(Arc<CancelState>);

impl RailCancel {
    /// Request cancellation; already started rail workers still quiesce.
    pub fn cancel(&self) {
        let _gate = self.0.publish_gate.lock().unwrap();
        self.0.cancelled.store(true, Ordering::Release);
    }

    /// Whether cancellation has been requested.
    pub fn is_cancelled(&self) -> bool {
        self.0.cancelled.load(Ordering::Acquire)
    }

    fn publish_if_live<T>(&self, publish: impl FnOnce() -> T) -> Result<T, RailReadError> {
        let _gate = self.0.publish_gate.lock().unwrap();
        if self.is_cancelled() {
            return Err(RailReadError::Cancelled);
        }
        Ok(publish())
    }
}

#[derive(Clone, Debug)]
struct StripeRead {
    index: u32,
    object_offset: usize,
    length: usize,
    packed_offset: usize,
}

#[derive(Clone, Debug)]
struct RailTask {
    route_index: usize,
    stripes: Vec<StripeRead>,
    packed_len: usize,
    dummy_len: usize,
}

struct RailPlan {
    size: usize,
    chunk_size: usize,
    checksums: Vec<String>,
    tasks: Vec<RailTask>,
    direct_staging: bool,
    contiguous_schedule: bool,
}

impl RailPlan {
    fn build(
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
        routes: &[RailRoute],
    ) -> Result<Self, RailReadError> {
        let size = usize::try_from(descriptor.size).map_err(|_| {
            RailReadError::InvalidPlacement("object size exceeds address space".into())
        })?;
        if descriptor.key.is_none()
            || placement.key != descriptor.key
            || descriptor.object_handle.is_empty()
            || descriptor.object_generation == 0
            || descriptor.layout_version == 0
        {
            return Err(RailReadError::InvalidPlacement(
                "descriptor identity and placement key do not match".into(),
            ));
        }
        if size == 0 {
            return Err(RailReadError::InvalidPlacement("empty RDMA object".into()));
        }
        let (stripe_count, chunk_size) = if descriptor.is_striped {
            let chunk_size = usize::try_from(descriptor.chunk_size).map_err(|_| {
                RailReadError::InvalidPlacement("chunk size exceeds address space".into())
            })?;
            if chunk_size == 0 || descriptor.stripe_count as usize != size.div_ceil(chunk_size) {
                return Err(RailReadError::InvalidPlacement(
                    "descriptor stripe count and chunk size disagree".into(),
                ));
            }
            (descriptor.stripe_count as usize, chunk_size)
        } else {
            (1, size)
        };
        if stripe_count > u16::MAX as usize || placement.chunks.len() != stripe_count {
            return Err(RailReadError::InvalidPlacement(
                "placement stripe count exceeds the wire limit or is incomplete".into(),
            ));
        }
        let mut ordered = vec![None; stripe_count];
        for chunk in &placement.chunks {
            let index = chunk.stripe_index as usize;
            if index >= stripe_count || ordered[index].is_some() {
                return Err(RailReadError::InvalidPlacement(format!(
                    "stripe {} is duplicate or out of bounds",
                    chunk.stripe_index
                )));
            }
            let offset = index * chunk_size;
            let length = (size - offset).min(chunk_size);
            if chunk.offset != offset as u64
                || chunk.length != length as u64
                || chunk.rdma_endpoint.is_empty()
            {
                return Err(RailReadError::InvalidPlacement(format!(
                    "stripe {index} has a wrong offset, length, or endpoint"
                )));
            }
            ordered[index] = Some(chunk);
        }
        let mut tasks: Vec<RailTask> = routes
            .iter()
            .enumerate()
            .filter(|(_, route)| route.enabled)
            .map(|(route_index, _)| RailTask {
                route_index,
                stripes: Vec::new(),
                packed_len: 0,
                dummy_len: 0,
            })
            .collect();
        let mut checksums = Vec::with_capacity(stripe_count);
        for (index, chunk) in ordered.into_iter().enumerate() {
            let chunk = chunk.ok_or_else(|| {
                RailReadError::InvalidPlacement(format!("missing stripe {index}"))
            })?;
            let offset = index * chunk_size;
            let length = (size - offset).min(chunk_size);
            let selected = tasks
                .iter()
                .enumerate()
                .filter(|(_, task)| {
                    routes[task.route_index].advertised_endpoint == chunk.rdma_endpoint
                })
                .min_by(|(_, left), (_, right)| {
                    let left_weight = routes[left.route_index].weight as u128;
                    let right_weight = routes[right.route_index].weight as u128;
                    ((left.packed_len as u128) * right_weight)
                        .cmp(&((right.packed_len as u128) * left_weight))
                        .then_with(|| right_weight.cmp(&left_weight))
                })
                .map(|(position, _)| position)
                .ok_or_else(|| {
                    RailReadError::InvalidPlacement(format!(
                        "no enabled rail for {}",
                        chunk.rdma_endpoint
                    ))
                })?;
            let task = &mut tasks[selected];
            task.stripes.push(StripeRead {
                index: index as u32,
                object_offset: offset,
                length,
                packed_offset: task.packed_len,
            });
            task.packed_len += length;
            checksums.push(chunk.checksum.clone());
        }
        tasks.retain(|task| !task.stripes.is_empty());
        let checksum_count = checksums
            .iter()
            .filter(|checksum| !checksum.is_empty())
            .count();
        if checksum_count != 0 && checksum_count != stripe_count {
            return Err(RailReadError::InvalidPlacement(
                "placement has only some stripe checksums".into(),
            ));
        }
        if tasks.is_empty() {
            return Err(RailReadError::InvalidPlacement("no rail selected".into()));
        }
        for task in &mut tasks {
            task.dummy_len = if task.stripes.len() == stripe_count {
                0
            } else {
                chunk_size.min(size)
            };
        }
        Ok(Self {
            size,
            chunk_size,
            checksums,
            tasks,
            direct_staging: false,
            contiguous_schedule: false,
        })
    }

    /// Repartition an already validated single-owner object into disjoint
    /// consecutive slices. Multi-owner layouts keep the original planner.
    fn contiguous_for_single_owner(
        mut self,
        placement: &pb::PlacementDescriptor,
        routes: &[RailRoute],
        direct_staging: bool,
    ) -> Self {
        let count = self.checksums.len();
        let rails = self.tasks.len();
        if rails < 2
            || rails > count
            || placement.chunks[0].node_id.trim().is_empty()
            || !placement.chunks.iter().all(|chunk| {
                chunk.rdma_endpoint == placement.chunks[0].rdma_endpoint
                    && chunk.node_id == placement.chunks[0].node_id
            })
        {
            return self;
        }
        self.tasks.sort_by_key(|task| task.route_index);
        let total_weight: u128 = self
            .tasks
            .iter()
            .map(|task| routes[task.route_index].weight as u128)
            .sum();
        let remaining = count - rails;
        let mut quotas: Vec<(usize, u128)> = self
            .tasks
            .iter()
            .map(|task| {
                let share = remaining as u128 * routes[task.route_index].weight as u128;
                (1 + (share / total_weight) as usize, share % total_weight)
            })
            .collect();
        let assigned: usize = quotas.iter().map(|quota| quota.0).sum();
        let mut residual_order: Vec<usize> = (0..rails).collect();
        residual_order.sort_by(|left, right| {
            quotas[*right]
                .1
                .cmp(&quotas[*left].1)
                .then_with(|| left.cmp(right))
        });
        for position in residual_order.into_iter().take(count - assigned) {
            quotas[position].0 += 1;
        }
        let mut stripe_index = 0;
        for (task, (stripe_quota, _)) in self.tasks.iter_mut().zip(quotas) {
            task.stripes.clear();
            task.packed_len = 0;
            task.dummy_len = self.chunk_size.min(self.size);
            for _ in 0..stripe_quota {
                let object_offset = stripe_index * self.chunk_size;
                let length = (self.size - object_offset).min(self.chunk_size);
                task.stripes.push(StripeRead {
                    index: stripe_index as u32,
                    object_offset,
                    length,
                    packed_offset: task.packed_len,
                });
                task.packed_len += length;
                stripe_index += 1;
            }
        }
        debug_assert_eq!(stripe_index, count);
        debug_assert_eq!(
            self.tasks.iter().map(|task| task.packed_len).sum::<usize>(),
            self.size
        );
        self.direct_staging = direct_staging;
        self.contiguous_schedule = true;
        self
    }
}

trait RailTransport: Sync {
    fn fetch(
        &self,
        route: &RailRoute,
        task: &RailTask,
        descriptor: &pb::ObjectDescriptor,
        timeout: Duration,
    ) -> Result<Vec<u8>, RailReadError>;

    fn fetch_into(
        &self,
        route: &RailRoute,
        task: &RailTask,
        descriptor: &pb::ObjectDescriptor,
        target: &mut [u8],
        timeout: Duration,
    ) -> Result<(), RailReadError> {
        let packed = self.fetch(route, task, descriptor, timeout)?;
        if packed.len() != target.len() {
            return Err(RailReadError::Incomplete {
                expected: target.len(),
                actual: packed.len(),
            });
        }
        target.copy_from_slice(&packed);
        Ok(())
    }
}

#[derive(Default)]
struct RailCounters {
    enabled: AtomicBool,
    cooldown_until: Mutex<Option<Instant>>,
    reads_ok: AtomicU64,
    reads_err: AtomicU64,
    bytes: AtomicU64,
    inflight_requests: AtomicU64,
    inflight_bytes: AtomicU64,
    peak_inflight_bytes: AtomicU64,
    registered_bytes: AtomicU64,
    peak_registered_bytes: AtomicU64,
    duration_us: AtomicU64,
}

impl RailCounters {
    fn cooldown_remaining(&self) -> Duration {
        self.cooldown_until
            .lock()
            .unwrap()
            .map(|until| until.saturating_duration_since(Instant::now()))
            .unwrap_or_default()
    }

    fn cooldown_ms(&self) -> u64 {
        self.cooldown_remaining().as_millis() as u64
    }

    fn is_available(&self) -> bool {
        self.enabled.load(Ordering::Acquire) && self.cooldown_remaining().is_zero()
    }
}

struct RailActivityGuard<'a> {
    counters: &'a RailCounters,
    bytes: u64,
    registered: u64,
}

impl<'a> RailActivityGuard<'a> {
    fn new(counters: &'a RailCounters, bytes: u64, registered: u64) -> Self {
        counters.inflight_requests.fetch_add(1, Ordering::Relaxed);
        let inflight = counters.inflight_bytes.fetch_add(bytes, Ordering::Relaxed) + bytes;
        counters
            .peak_inflight_bytes
            .fetch_max(inflight, Ordering::Relaxed);
        let pinned = counters
            .registered_bytes
            .fetch_add(registered, Ordering::Relaxed)
            + registered;
        counters
            .peak_registered_bytes
            .fetch_max(pinned, Ordering::Relaxed);
        Self {
            counters,
            bytes,
            registered,
        }
    }
}

impl Drop for RailActivityGuard<'_> {
    fn drop(&mut self) {
        self.counters
            .inflight_requests
            .fetch_sub(1, Ordering::Relaxed);
        self.counters
            .inflight_bytes
            .fetch_sub(self.bytes, Ordering::Relaxed);
        self.counters
            .registered_bytes
            .fetch_sub(self.registered, Ordering::Relaxed);
    }
}

/// One rail's cumulative and current metrics.
#[derive(Clone, Debug)]
pub struct RailSnapshot {
    /// Configured rail name.
    pub id: String,
    /// Dialed control listener.
    pub listener: String,
    /// Local Verbs device name.
    pub device: String,
    /// Cached NUMA and PCIe placement for the local device.
    pub topology: RailTopology,
    /// Whether this rail is enabled and outside its failure cooldown.
    pub healthy: bool,
    /// Milliseconds until a failed rail becomes selectable again.
    pub cooldown_ms: u64,
    /// Successful stripe-subset requests.
    pub reads_ok: u64,
    /// Failed stripe-subset requests.
    pub reads_err: u64,
    /// Verified payload bytes returned by this rail.
    pub bytes: u64,
    /// Requests currently executing on this rail.
    pub inflight_requests: u64,
    /// Payload bytes currently assigned to executing requests.
    pub inflight_bytes: u64,
    /// Maximum observed in-flight payload bytes.
    pub peak_inflight_bytes: u64,
    /// Reserved MR bytes while a task is active (not a hardware pin counter).
    pub registered_bytes: u64,
    /// Maximum observed reserved MR length.
    pub peak_registered_bytes: u64,
    /// Cumulative task duration in microseconds.
    pub duration_us: u64,
}

#[derive(Default)]
struct BudgetState {
    active_reads: usize,
    staging_bytes: u64,
    registered_bytes: u64,
    inflight_bytes: u64,
    rail_active_reads: Vec<usize>,
    rail_inflight_bytes: Vec<u64>,
}

// Preserve the RailLimits struct API while bounding one request's worker/QP count.
const MAX_RAIL_TASKS_PER_READ: usize = 32;

/// Bounded multi-rail reader; one active request uses one QP per chosen rail.
pub struct RailReader {
    routes: Vec<RailRoute>,
    discovered_capabilities: Option<Vec<pb::RdmaRailEndpoint>>,
    discovered_owners: Option<HashSet<(String, String)>>,
    topologies: Vec<RailTopology>,
    limits: RailLimits,
    counters: Vec<RailCounters>,
    mode_counts: [AtomicU64; 4],
    budget: Arc<Mutex<BudgetState>>,
    connection_pool: Option<ConnectionPool<ConnectionKey, RdmaClient>>,
    configuration_generation: u64,
}

/// Counts admitted reads by the plan actually selected after route filtering.
#[derive(Clone, Copy, Debug, Default)]
pub struct RailModeCounts {
    pub one: u64,
    pub packed_greedy: u64,
    pub packed_contiguous: u64,
    pub direct: u64,
}

/// Validated per-Rail stripe plan for diagnostic and benchmark provenance.
#[derive(Clone, Debug)]
pub struct RailAssignmentSnapshot {
    pub id: String,
    pub stripe_indices: Vec<u32>,
    pub bytes: usize,
}

struct BudgetGuard {
    state: Arc<Mutex<BudgetState>>,
    staging: u64,
    transient_staging: u64,
    registered: u64,
    inflight: u64,
    rail_reservations: Vec<(usize, u64)>,
}

impl BudgetGuard {
    fn finish_transfer(&mut self) {
        let mut budget = self.state.lock().unwrap();
        budget.staging_bytes -= self.transient_staging;
        budget.registered_bytes -= self.registered;
        budget.inflight_bytes -= self.inflight;
        for (index, bytes) in self.rail_reservations.drain(..) {
            budget.rail_active_reads[index] -= 1;
            budget.rail_inflight_bytes[index] -= bytes;
        }
        self.staging -= self.transient_staging;
        self.transient_staging = 0;
        self.registered = 0;
        self.inflight = 0;
    }
}

impl Drop for BudgetGuard {
    fn drop(&mut self) {
        let mut budget = self.state.lock().unwrap();
        budget.active_reads -= 1;
        budget.staging_bytes -= self.staging;
        budget.registered_bytes -= self.registered;
        budget.inflight_bytes -= self.inflight;
        for (index, bytes) in &self.rail_reservations {
            budget.rail_active_reads[*index] -= 1;
            budget.rail_inflight_bytes[*index] -= *bytes;
        }
    }
}

/// A verified private payload that retains its staging and active-read budget
/// until the caller publishes or discards the bytes.
pub struct StagedRailRead {
    bytes: Vec<u8>,
    _budget: BudgetGuard,
}

impl StagedRailRead {
    /// Complete object bytes in descriptor order.
    pub fn as_bytes(&self) -> &[u8] {
        &self.bytes
    }
}

fn effective_registered_budget(configured: u64, soft_memlock_limit: Option<u64>) -> u64 {
    match soft_memlock_limit {
        Some(limit) => configured.min(limit.saturating_sub(limit / 5)),
        None => configured,
    }
}

#[cfg(target_os = "linux")]
fn process_memlock_limit() -> Option<u64> {
    let mut limit: libc::rlimit = unsafe { std::mem::zeroed() };
    if unsafe { libc::getrlimit(libc::RLIMIT_MEMLOCK, &mut limit) } != 0
        || limit.rlim_cur == libc::RLIM_INFINITY
    {
        return None;
    }
    Some(limit.rlim_cur)
}

#[cfg(not(target_os = "linux"))]
fn process_memlock_limit() -> Option<u64> {
    None
}

impl RailReader {
    /// Resolve remote listeners advertised by `LookupObject` against known
    /// local fabrics. Existing manual routes remain available for older servers.
    pub fn discover_from_placement(
        placement: &pb::PlacementDescriptor,
        local_paths: &[LocalRailPath],
        limits: RailLimits,
    ) -> Result<Self, RailReadError> {
        if placement.chunks.is_empty() || placement.rdma_rails.is_empty() {
            return Err(RailReadError::InvalidPlacement(
                "placement has no rail capabilities; use manual routes for older servers".into(),
            ));
        }
        let mut local_ids = HashSet::new();
        let mut local_fabrics = HashSet::new();
        let mut local_ports = HashSet::new();
        for path in local_paths {
            if path.id.trim().is_empty()
                || path.fabric_id.trim().is_empty()
                || path.device.trim().is_empty()
                || path.port == 0
                || !local_ids.insert(path.id.as_str())
                || !local_fabrics.insert(path.fabric_id.as_str())
                || !local_ports.insert((path.device.as_str(), path.port))
            {
                return Err(RailReadError::InvalidPlacement(
                    "local rail IDs, fabrics, devices and ports must be distinct".into(),
                ));
            }
        }
        let mut owners = HashSet::new();
        let mut endpoint_owners = HashMap::new();
        for chunk in &placement.chunks {
            if chunk.node_id.trim().is_empty() || chunk.rdma_endpoint.trim().is_empty() {
                return Err(RailReadError::InvalidPlacement(
                    "stripe owner or RDMA endpoint is empty".into(),
                ));
            }
            if let Some(previous) = endpoint_owners.insert(&chunk.rdma_endpoint, &chunk.node_id) {
                if previous != &chunk.node_id {
                    return Err(RailReadError::InvalidPlacement(
                        "one RDMA endpoint identifies two storage owners".into(),
                    ));
                }
            }
            owners.insert((&chunk.node_id, &chunk.rdma_endpoint));
        }
        let mut seen_fabrics = HashSet::new();
        let mut seen_listeners = HashSet::new();
        let mut routes = Vec::new();
        for rail in &placement.rdma_rails {
            if rail.node_id.trim().is_empty()
                || rail.advertised_endpoint.trim().is_empty()
                || rail.fabric_id.trim().is_empty()
                || rail.listener_endpoint.trim().is_empty()
                || !owners.contains(&(&rail.node_id, &rail.advertised_endpoint))
                || !seen_fabrics.insert((&rail.node_id, &rail.advertised_endpoint, &rail.fabric_id))
                || !seen_listeners.insert(&rail.listener_endpoint)
            {
                return Err(RailReadError::InvalidPlacement(
                    "advertised rail is blank, duplicated, or not an object owner".into(),
                ));
            }
            if let Some(path) = local_paths
                .iter()
                .find(|path| path.fabric_id == rail.fabric_id)
            {
                let connection = RdmaClientConfig::new(&rail.listener_endpoint, &path.device)
                    .with_port(path.port)
                    .with_gid_index(path.gid_index);
                routes.push(
                    RailRoute::new(
                        format!("{}/{}/{}", rail.node_id, rail.advertised_endpoint, path.id),
                        &rail.advertised_endpoint,
                        connection,
                    )
                    .with_weight(path.weight),
                );
            }
        }
        if owners.iter().any(|(_, endpoint)| {
            !routes
                .iter()
                .any(|route| route.advertised_endpoint == **endpoint)
        }) {
            return Err(RailReadError::InvalidPlacement(
                "no matching local fabric for a storage owner".into(),
            ));
        }
        let mut reader = Self::new(routes, limits)?;
        reader.discovered_capabilities = Some(placement.rdma_rails.clone());
        reader.discovered_owners = Some(
            owners
                .into_iter()
                .map(|(node, endpoint)| (node.clone(), endpoint.clone()))
                .collect(),
        );
        Ok(reader)
    }

    /// Validate routes and create an RDMA reader without opening connections.
    pub fn new(routes: Vec<RailRoute>, mut limits: RailLimits) -> Result<Self, RailReadError> {
        if routes.is_empty()
            || limits.max_active_reads == 0
            || limits.max_active_reads_per_rail == 0
        {
            return Err(RailReadError::InvalidPlacement(
                "at least one rail and one active read slot are required".into(),
            ));
        }
        if routes.len() > MAX_RAIL_TASKS_PER_READ {
            return Err(RailReadError::ResourceExhausted(format!(
                "configured rail count exceeds per-read task limit ({})",
                MAX_RAIL_TASKS_PER_READ
            )));
        }
        let mut ids = HashSet::new();
        let mut paths = HashSet::new();
        let mut local_ports = HashSet::new();
        for route in &routes {
            if route.id.is_empty()
                || route.advertised_endpoint.is_empty()
                || route.connection.endpoint.is_empty()
                || route.weight == 0
                || !ids.insert(route.id.clone())
                || !paths.insert((
                    route.advertised_endpoint.clone(),
                    route.connection.endpoint.clone(),
                ))
                || !local_ports.insert((
                    route.advertised_endpoint.clone(),
                    route.connection.device.clone(),
                    route.connection.port,
                ))
            {
                return Err(RailReadError::InvalidPlacement(
                    "rail IDs, listeners, and local ports must identify distinct paths".into(),
                ));
            }
        }
        let topologies = routes
            .iter()
            .map(|route| {
                read_topology_from(Path::new("/sys/class/infiniband"), &route.connection.device)
            })
            .collect();
        let counters = routes
            .iter()
            .map(|route| RailCounters {
                enabled: AtomicBool::new(route.enabled),
                ..RailCounters::default()
            })
            .collect();
        let route_count = routes.len();
        limits.max_registered_bytes =
            effective_registered_budget(limits.max_registered_bytes, process_memlock_limit());
        Ok(Self {
            routes,
            connection_pool: None,
            configuration_generation: 0,
            discovered_capabilities: None,
            discovered_owners: None,
            topologies,
            limits,
            counters,
            mode_counts: std::array::from_fn(|_| AtomicU64::new(0)),
            budget: Arc::new(Mutex::new(BudgetState {
                rail_active_reads: vec![0; route_count],
                rail_inflight_bytes: vec![0; route_count],
                ..BudgetState::default()
            })),
        })
    }

    /// Opt in to bounded persistent connections for striped reads only.
    /// Unstriped reads retain per-request QP teardown and do not use the pool.
    /// Routes and their generation
    /// remain immutable; rebuilding the reader creates a separate pool.
    /// Admission fails immediately when every slot is active or retiring.
    /// Idle connections for other routes may be destroyed to make room.
    pub fn with_connection_pool(
        mut self,
        options: RailConnectionPoolOptions,
    ) -> Result<Self, RailReadError> {
        if options.max_live_connections == 0 {
            return Err(RailReadError::ResourceExhausted(
                "RDMA connection pool needs a positive live connection limit".into(),
            ));
        }
        self.connection_pool = Some(ConnectionPool::new(options.max_live_connections));
        self.configuration_generation = options.configuration_generation;
        Ok(self)
    }

    /// Return typed connection lifecycle observations, or `None` in legacy mode.
    pub fn connection_pool_snapshot(&self) -> Option<RailConnectionPoolSnapshot> {
        self.connection_pool.as_ref().map(ConnectionPool::snapshot)
    }

    /// The resolved route map used by this reader, including discovered listeners.
    pub fn routes(&self) -> &[RailRoute] {
        &self.routes
    }

    pub fn mode_counts(&self) -> RailModeCounts {
        RailModeCounts {
            one: self.mode_counts[0].load(Ordering::Relaxed),
            packed_greedy: self.mode_counts[1].load(Ordering::Relaxed),
            packed_contiguous: self.mode_counts[2].load(Ordering::Relaxed),
            direct: self.mode_counts[3].load(Ordering::Relaxed),
        }
    }

    pub fn describe_assignment(
        &self,
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
    ) -> Result<Vec<RailAssignmentSnapshot>, RailReadError> {
        let mut available = self.routes.clone();
        for (route, counters) in available.iter_mut().zip(&self.counters) {
            route.enabled = counters.is_available();
        }
        let mut plan = RailPlan::build(descriptor, placement, &available)?;
        if self.limits.direct_staging || self.limits.contiguous_schedule {
            plan =
                plan.contiguous_for_single_owner(placement, &available, self.limits.direct_staging);
        }
        Ok(plan
            .tasks
            .iter()
            .map(|task| RailAssignmentSnapshot {
                id: self.routes[task.route_index].id.clone(),
                stripe_indices: task.stripes.iter().map(|stripe| stripe.index).collect(),
                bytes: task.packed_len,
            })
            .collect())
    }

    /// Read per-rail counters without blocking in-flight transfers.
    pub fn snapshots(&self) -> Vec<RailSnapshot> {
        self.routes
            .iter()
            .zip(&self.counters)
            .enumerate()
            .map(|(index, (route, counters))| RailSnapshot {
                id: route.id.clone(),
                listener: route.connection.endpoint.clone(),
                device: route.connection.device.clone(),
                topology: self.topologies[index].clone(),
                healthy: counters.is_available(),
                cooldown_ms: counters.cooldown_ms(),
                reads_ok: counters.reads_ok.load(Ordering::Relaxed),
                reads_err: counters.reads_err.load(Ordering::Relaxed),
                bytes: counters.bytes.load(Ordering::Relaxed),
                inflight_requests: counters.inflight_requests.load(Ordering::Relaxed),
                inflight_bytes: counters.inflight_bytes.load(Ordering::Relaxed),
                peak_inflight_bytes: counters.peak_inflight_bytes.load(Ordering::Relaxed),
                registered_bytes: counters.registered_bytes.load(Ordering::Relaxed),
                peak_registered_bytes: counters.peak_registered_bytes.load(Ordering::Relaxed),
                duration_us: counters.duration_us.load(Ordering::Relaxed),
            })
            .collect()
    }

    /// Enable or disable one Rail without modifying object placement.
    pub fn set_enabled(&self, id: &str, enabled: bool) -> bool {
        let Some(index) = self.routes.iter().position(|route| route.id == id) else {
            return false;
        };
        let counters = &self.counters[index];
        counters.enabled.store(enabled, Ordering::Release);
        if enabled {
            *counters.cooldown_until.lock().unwrap() = None;
        }
        true
    }

    fn reserve(&self, plan: &RailPlan) -> Result<BudgetGuard, RailReadError> {
        let dummy_bytes = plan
            .tasks
            .iter()
            .try_fold(0u64, |sum, task| sum.checked_add(task.dummy_len as u64))
            .ok_or_else(|| {
                RailReadError::ResourceExhausted("dummy staging size overflow".into())
            })?;
        let registered = plan
            .tasks
            .iter()
            .try_fold(0u64, |sum, task| {
                task.packed_len
                    .checked_add(task.dummy_len)
                    .and_then(|bytes| sum.checked_add(bytes as u64))
            })
            .ok_or_else(|| RailReadError::ResourceExhausted("registration size overflow".into()))?;
        let transient_staging = if plan.direct_staging {
            dummy_bytes
        } else {
            registered
        };
        let staging = transient_staging
            .checked_add(plan.size as u64)
            .ok_or_else(|| RailReadError::ResourceExhausted("staging size overflow".into()))?;
        let inflight = plan.size as u64;
        let rail_reservations: Vec<(usize, u64)> = plan
            .tasks
            .iter()
            .map(|task| (task.route_index, task.packed_len as u64))
            .collect();
        let mut budget = self.budget.lock().unwrap();
        if budget.registered_bytes.saturating_add(registered) > self.limits.max_registered_bytes {
            return Err(RailReadError::ResourceExhausted(format!(
                "registered bytes exceed configured or process memlock budget ({} bytes)",
                self.limits.max_registered_bytes
            )));
        }
        if budget.active_reads >= self.limits.max_active_reads
            || budget.staging_bytes.saturating_add(staging) > self.limits.max_staging_bytes
            || budget.inflight_bytes.saturating_add(inflight) > self.limits.max_inflight_bytes
        {
            return Err(RailReadError::ResourceExhausted(
                "active reads, staging, registration, or in-flight bytes".into(),
            ));
        }
        if rail_reservations.iter().any(|(index, bytes)| {
            budget.rail_active_reads[*index] >= self.limits.max_active_reads_per_rail
                || budget.rail_inflight_bytes[*index].saturating_add(*bytes)
                    > self.limits.max_inflight_bytes_per_rail
        }) {
            return Err(RailReadError::ResourceExhausted(
                "per-rail active tasks or aggregate in-flight bytes".into(),
            ));
        }
        budget.active_reads += 1;
        budget.staging_bytes += staging;
        budget.registered_bytes += registered;
        budget.inflight_bytes += inflight;
        for (index, bytes) in &rail_reservations {
            budget.rail_active_reads[*index] += 1;
            budget.rail_inflight_bytes[*index] += *bytes;
        }
        Ok(BudgetGuard {
            state: Arc::clone(&self.budget),
            staging,
            transient_staging,
            registered,
            inflight,
            rail_reservations,
        })
    }

    fn fetch_direct_staged<T: RailTransport>(
        &self,
        plan: &RailPlan,
        descriptor: &pb::ObjectDescriptor,
        transport: &T,
        cancel: Option<&RailCancel>,
    ) -> Result<(Vec<u8>, u128), RailReadError> {
        let workers_start = Instant::now();
        let mut staged = Vec::new();
        staged
            .try_reserve_exact(plan.size)
            .map_err(|error| RailReadError::ResourceExhausted(error.to_string()))?;
        staged.resize(plan.size, 0);
        let mut remainder = staged.as_mut_slice();
        let mut targets = Vec::with_capacity(plan.tasks.len());
        for task in &plan.tasks {
            let (target, tail) = remainder.split_at_mut(task.packed_len);
            targets.push(target);
            remainder = tail;
        }
        if !remainder.is_empty() {
            return Err(RailReadError::InvalidPlacement(
                "direct Rail slices do not cover the object".into(),
            ));
        }
        let results = std::thread::scope(|scope| {
            let handles: Vec<_> = plan
                .tasks
                .iter()
                .zip(targets)
                .map(|(task, target)| {
                    let route = &self.routes[task.route_index];
                    let counters = &self.counters[task.route_index];
                    scope.spawn(move || {
                        let started = Instant::now();
                        let _activity = RailActivityGuard::new(
                            counters,
                            task.packed_len as u64,
                            (task.packed_len + task.dummy_len) as u64,
                        );
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            transport.fetch_into(
                                route,
                                task,
                                descriptor,
                                target,
                                self.limits.io_timeout,
                            )
                        }))
                        .unwrap_or(Err(RailReadError::WorkerPanic));
                        counters
                            .duration_us
                            .fetch_add(started.elapsed().as_micros() as u64, Ordering::Relaxed);
                        if result.is_ok() {
                            counters.reads_ok.fetch_add(1, Ordering::Relaxed);
                            counters
                                .bytes
                                .fetch_add(task.packed_len as u64, Ordering::Relaxed);
                        } else {
                            counters.reads_err.fetch_add(1, Ordering::Relaxed);
                            if matches!(
                                &result,
                                Err(RailReadError::Transport(_)
                                    | RailReadError::Incomplete { .. }
                                    | RailReadError::WorkerPanic)
                            ) {
                                *counters.cooldown_until.lock().unwrap() =
                                    Some(Instant::now() + self.limits.rail_cooldown);
                            }
                        }
                        result
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or(Err(RailReadError::WorkerPanic)))
                .collect::<Vec<_>>()
        });
        let workers_us = workers_start.elapsed().as_micros();
        if cancel.is_some_and(RailCancel::is_cancelled) {
            return Err(RailReadError::Cancelled);
        }
        for result in results {
            result?;
        }
        Ok((staged, workers_us))
    }

    fn read_staged_with<T: RailTransport>(
        &self,
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
        transport: &T,
        cancel: Option<&RailCancel>,
    ) -> Result<StagedRailRead, RailReadError> {
        let timing_start = Instant::now();
        if let Some(expected) = &self.discovered_owners {
            let current: HashSet<(&str, &str)> = placement
                .chunks
                .iter()
                .map(|chunk| (chunk.node_id.as_str(), chunk.rdma_endpoint.as_str()))
                .collect();
            if current.len() != expected.len()
                || expected
                    .iter()
                    .any(|(node, endpoint)| !current.contains(&(node.as_str(), endpoint.as_str())))
            {
                return Err(RailReadError::InvalidPlacement(
                    "stripe owner changed; rebuild the discovered reader".into(),
                ));
            }
        }
        if self
            .discovered_capabilities
            .as_ref()
            .is_some_and(|expected| expected != &placement.rdma_rails)
        {
            return Err(RailReadError::InvalidPlacement(
                "advertised rail capabilities changed; rebuild the discovered reader".into(),
            ));
        }
        let mut available = self.routes.clone();
        for (route, counters) in available.iter_mut().zip(&self.counters) {
            route.enabled = counters.is_available();
        }
        let mut plan = RailPlan::build(descriptor, placement, &available)?;
        if self.limits.direct_staging || self.limits.contiguous_schedule {
            plan =
                plan.contiguous_for_single_owner(placement, &available, self.limits.direct_staging);
        }
        let plan_us = timing_start.elapsed().as_micros();
        let reserve_start = Instant::now();
        let mut budget = self.reserve(&plan)?;
        if cancel.is_some_and(RailCancel::is_cancelled) {
            return Err(RailReadError::Cancelled);
        }
        let mode_index = if plan.tasks.len() == 1 {
            0
        } else if plan.direct_staging {
            3
        } else if plan.contiguous_schedule {
            2
        } else {
            1
        };
        self.mode_counts[mode_index].fetch_add(1, Ordering::Relaxed);
        let reserve_us = reserve_start.elapsed().as_micros();
        if plan.direct_staging {
            let (staged, workers_us) =
                self.fetch_direct_staged(&plan, descriptor, transport, cancel)?;
            let checksum_start = Instant::now();
            for (index, checksum) in plan.checksums.iter().enumerate() {
                if checksum.is_empty() {
                    continue;
                }
                let start = index * plan.chunk_size;
                let end = (start + plan.chunk_size).min(plan.size);
                let actual = format!("{:016x}", twox_hash::xxh3::hash64(&staged[start..end]));
                if !checksum.eq_ignore_ascii_case(&actual) {
                    return Err(RailReadError::Checksum {
                        stripe: index as u32,
                    });
                }
            }
            let checksum_us = checksum_start.elapsed().as_micros();
            let finish_start = Instant::now();
            budget.finish_transfer();
            if crate::rail_timing_enabled() {
                eprintln!(
                    "RAIL_STAGED_TIMING bytes={} rails={} plan_us={plan_us} reserve_us={reserve_us} workers_us={workers_us} assembly_us=0 checksum_us={checksum_us} finish_us={} total_us={}",
                    plan.size,
                    plan.tasks.len(),
                    finish_start.elapsed().as_micros(),
                    timing_start.elapsed().as_micros(),
                );
            }
            return Ok(StagedRailRead {
                bytes: staged,
                _budget: budget,
            });
        }
        let workers_start = Instant::now();
        let results = std::thread::scope(|scope| {
            let handles: Vec<_> = plan
                .tasks
                .iter()
                .map(|task| {
                    let route = &self.routes[task.route_index];
                    let counters = &self.counters[task.route_index];
                    scope.spawn(move || {
                        let started = Instant::now();
                        let _activity = RailActivityGuard::new(
                            counters,
                            task.packed_len as u64,
                            (task.packed_len + task.dummy_len) as u64,
                        );
                        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                            transport.fetch(route, task, descriptor, self.limits.io_timeout)
                        }))
                        .unwrap_or(Err(RailReadError::WorkerPanic));
                        counters
                            .duration_us
                            .fetch_add(started.elapsed().as_micros() as u64, Ordering::Relaxed);
                        if result.is_ok() {
                            counters.reads_ok.fetch_add(1, Ordering::Relaxed);
                            counters
                                .bytes
                                .fetch_add(task.packed_len as u64, Ordering::Relaxed);
                        } else {
                            counters.reads_err.fetch_add(1, Ordering::Relaxed);
                            if matches!(
                                &result,
                                Err(RailReadError::Transport(_)
                                    | RailReadError::Incomplete { .. }
                                    | RailReadError::WorkerPanic)
                            ) {
                                *counters.cooldown_until.lock().unwrap() =
                                    Some(Instant::now() + self.limits.rail_cooldown);
                            }
                        }
                        result
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or(Err(RailReadError::WorkerPanic)))
                .collect::<Vec<_>>()
        });
        let workers_us = workers_start.elapsed().as_micros();
        if cancel.is_some_and(RailCancel::is_cancelled) {
            return Err(RailReadError::Cancelled);
        }
        let assembly_start = Instant::now();
        let single_ordered = plan.tasks.len() == 1
            && plan.tasks[0].packed_len == plan.size
            && plan.tasks[0]
                .stripes
                .iter()
                .all(|stripe| stripe.packed_offset == stripe.object_offset);
        let staged = if single_ordered {
            // The transfer has completed and its temporary MR is deregistered.
            // This private buffer is exactly the object in descriptor order.
            let packed = results.into_iter().next().expect("one planned task")?;
            if packed.len() != plan.size {
                return Err(RailReadError::Incomplete {
                    expected: plan.size,
                    actual: packed.len(),
                });
            }
            packed
        } else {
            let mut staged = Vec::new();
            staged
                .try_reserve_exact(plan.size)
                .map_err(|error| RailReadError::ResourceExhausted(error.to_string()))?;
            staged.resize(plan.size, 0);
            for (task, result) in plan.tasks.iter().zip(results) {
                let packed = result?;
                if packed.len() != task.packed_len {
                    return Err(RailReadError::Incomplete {
                        expected: task.packed_len,
                        actual: packed.len(),
                    });
                }
                for stripe in &task.stripes {
                    staged[stripe.object_offset..stripe.object_offset + stripe.length]
                        .copy_from_slice(
                            &packed[stripe.packed_offset..stripe.packed_offset + stripe.length],
                        );
                }
            }
            staged
        };
        let assembly_us = assembly_start.elapsed().as_micros();
        let checksum_start = Instant::now();
        for (index, checksum) in plan.checksums.iter().enumerate() {
            if checksum.is_empty() {
                continue;
            }
            let start = index * plan.chunk_size;
            let end = (start + plan.chunk_size).min(plan.size);
            let actual = format!("{:016x}", twox_hash::xxh3::hash64(&staged[start..end]));
            if !checksum.eq_ignore_ascii_case(&actual) {
                return Err(RailReadError::Checksum {
                    stripe: index as u32,
                });
            }
        }
        let checksum_us = checksum_start.elapsed().as_micros();
        let finish_start = Instant::now();
        budget.finish_transfer();
        if crate::rail_timing_enabled() {
            eprintln!(
                "RAIL_STAGED_TIMING bytes={} rails={} plan_us={plan_us} reserve_us={reserve_us} workers_us={workers_us} assembly_us={assembly_us} checksum_us={checksum_us} finish_us={} total_us={}",
                plan.size,
                plan.tasks.len(),
                finish_start.elapsed().as_micros(),
                timing_start.elapsed().as_micros(),
            );
        }
        Ok(StagedRailRead {
            bytes: staged,
            _budget: budget,
        })
    }

    fn read_into_with<T: RailTransport>(
        &self,
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
        destination: &mut [u8],
        transport: &T,
        cancel: Option<&RailCancel>,
    ) -> Result<usize, RailReadError> {
        let size = usize::try_from(descriptor.size).map_err(|_| {
            RailReadError::InvalidPlacement("object size exceeds address space".into())
        })?;
        if destination.len() < size {
            return Err(RailReadError::BufferTooSmall {
                need: size,
                have: destination.len(),
            });
        }
        let staged = self.read_staged_with(descriptor, placement, transport, cancel)?;
        if let Some(cancel) = cancel {
            cancel.publish_if_live(|| destination[..size].copy_from_slice(staged.as_bytes()))?;
        } else {
            destination[..size].copy_from_slice(staged.as_bytes());
        }
        Ok(size)
    }

    /// Return a complete private object buffer after all rails and checksums pass.
    /// Callers that require a stable version must perform a post-read lookup
    /// before publishing it, as `KvClient::read_multi_rail_into` does.
    pub fn read_staged(
        &self,
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
        cancel: Option<&RailCancel>,
    ) -> Result<StagedRailRead, RailReadError> {
        let transport = VerbsTransport::new(self, placement, descriptor)?;
        self.read_staged_with(descriptor, placement, &transport, cancel)
    }

    /// Copy a completed read into the caller's buffer. This low-level method
    /// validates each server-side descriptor request but does not re-lookup the
    /// object after transfer; use `KvClient::read_multi_rail_into` for that.
    pub fn read_into(
        &self,
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
        destination: &mut [u8],
        cancel: Option<&RailCancel>,
    ) -> Result<usize, RailReadError> {
        let transport = VerbsTransport::new(self, placement, descriptor)?;
        self.read_into_with(descriptor, placement, destination, &transport, cancel)
    }
}

fn build_sge_segments(
    task: &RailTask,
    descriptor: &pb::ObjectDescriptor,
    base: u64,
    rkey: u32,
) -> Result<Vec<(u64, u32, u64)>, RailReadError> {
    let registered_len = task
        .packed_len
        .checked_add(task.dummy_len)
        .ok_or_else(|| RailReadError::InvalidPlacement("registered size overflow".into()))?;
    let registered_end = base
        .checked_add(registered_len as u64)
        .ok_or_else(|| RailReadError::InvalidPlacement("registered address overflow".into()))?;
    let mut segments = Vec::with_capacity(descriptor.stripe_count as usize);
    let mut owned = task.stripes.iter().peekable();
    for index in 0..descriptor.stripe_count as usize {
        let offset = index
            .checked_mul(descriptor.chunk_size as usize)
            .ok_or_else(|| RailReadError::InvalidPlacement("stripe offset overflow".into()))?;
        let length = (descriptor.size as usize - offset).min(descriptor.chunk_size as usize);
        let local_offset = if owned
            .peek()
            .is_some_and(|stripe| stripe.index as usize == index)
        {
            owned.next().expect("owned stripe").packed_offset
        } else {
            task.packed_len
        };
        let address = base
            .checked_add(local_offset as u64)
            .ok_or_else(|| RailReadError::InvalidPlacement("SGE address overflow".into()))?;
        if address
            .checked_add(length as u64)
            .is_none_or(|end| end > registered_end)
        {
            return Err(RailReadError::InvalidPlacement(
                "SGE maps outside registered memory".into(),
            ));
        }
        segments.push((address, rkey, length as u64));
    }
    Ok(segments)
}

#[derive(Clone, Copy)]
struct SgeRegion {
    addr: u64,
    rkey: u32,
    len: usize,
}

fn build_sge_segments_direct(
    task: &RailTask,
    descriptor: &pb::ObjectDescriptor,
    target: SgeRegion,
    dummy: SgeRegion,
) -> Result<Vec<(u64, u32, u64)>, RailReadError> {
    if target.len != task.packed_len || dummy.len < task.dummy_len {
        return Err(RailReadError::InvalidPlacement(
            "direct SGE registration does not cover the task".into(),
        ));
    }
    let size = usize::try_from(descriptor.size)
        .map_err(|_| RailReadError::InvalidPlacement("object size exceeds address space".into()))?;
    let chunk_size = usize::try_from(descriptor.chunk_size)
        .map_err(|_| RailReadError::InvalidPlacement("chunk size exceeds address space".into()))?;
    let mut segments = Vec::with_capacity(descriptor.stripe_count as usize);
    let mut owned = task.stripes.iter().peekable();
    for index in 0..descriptor.stripe_count as usize {
        let offset = index
            .checked_mul(chunk_size)
            .ok_or_else(|| RailReadError::InvalidPlacement("stripe offset overflow".into()))?;
        let length = size
            .checked_sub(offset)
            .ok_or_else(|| RailReadError::InvalidPlacement("stripe offset exceeds size".into()))?
            .min(chunk_size);
        let (region, local_offset) = if owned
            .peek()
            .is_some_and(|stripe| stripe.index as usize == index)
        {
            (target, owned.next().expect("owned stripe").packed_offset)
        } else {
            (dummy, 0)
        };
        let end = local_offset
            .checked_add(length)
            .ok_or_else(|| RailReadError::InvalidPlacement("SGE range overflow".into()))?;
        if end > region.len {
            return Err(RailReadError::InvalidPlacement(
                "direct SGE maps outside registered memory".into(),
            ));
        }
        let address = region
            .addr
            .checked_add(local_offset as u64)
            .ok_or_else(|| RailReadError::InvalidPlacement("SGE address overflow".into()))?;
        segments.push((address, region.rkey, length as u64));
    }
    if owned.next().is_some() {
        return Err(RailReadError::InvalidPlacement(
            "direct SGE left an owned stripe unmapped".into(),
        ));
    }
    Ok(segments)
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct ConnectionKey {
    route_id: String,
    advertised_endpoint: String,
    owner_node_id: String,
    listener: String,
    device: String,
    port: u8,
    gid_index: u8,
    configuration_generation: u64,
}

impl ConnectionKey {
    fn new(route: &RailRoute, owner: &str, configuration_generation: u64) -> Self {
        Self {
            route_id: route.id.clone(),
            advertised_endpoint: route.advertised_endpoint.clone(),
            owner_node_id: owner.into(),
            listener: route.connection.endpoint.clone(),
            device: route.connection.device.clone(),
            port: route.connection.port,
            gid_index: route.connection.gid_index,
            configuration_generation,
        }
    }
}

struct VerbsTransport<'a> {
    pool: Option<&'a ConnectionPool<ConnectionKey, RdmaClient>>,
    configuration_generation: u64,
    owners: HashMap<&'a str, &'a str>,
}

impl<'a> VerbsTransport<'a> {
    fn new(
        reader: &'a RailReader,
        placement: &'a pb::PlacementDescriptor,
        descriptor: &pb::ObjectDescriptor,
    ) -> Result<Self, RailReadError> {
        // Full-descriptor GET reports server WRITE/segment counts, and its
        // server fallback has not established the terminal-ACK guarantee
        // required for reuse. Keep that entire path on legacy QP-before-MR
        // teardown, including bypassing pooled ownership/admission checks.
        let pool = reader
            .connection_pool
            .as_ref()
            .filter(|_| descriptor.is_striped);
        let mut owners = HashMap::new();
        if pool.is_some() {
            for chunk in &placement.chunks {
                let owner = chunk.node_id.as_str();
                if owner.trim().is_empty()
                    || owners
                        .insert(chunk.rdma_endpoint.as_str(), owner)
                        .is_some_and(|previous| previous != owner)
                {
                    return Err(RailReadError::InvalidPlacement(
                        "pooled routes require one nonempty owner per advertised endpoint".into(),
                    ));
                }
            }
        }
        Ok(Self {
            pool,
            configuration_generation: reader.configuration_generation,
            owners,
        })
    }

    fn acquire(
        &self,
        route: &RailRoute,
        timeout: Duration,
    ) -> Result<Lease<ConnectionKey, RdmaClient>, RailReadError> {
        let open = || {
            RdmaClient::connect(route.connection.clone().with_io_timeout(timeout))
                .map_err(|error| RailReadError::Transport(error.to_string()))
        };
        if let Some(pool) = self.pool {
            let owner = self
                .owners
                .get(route.advertised_endpoint.as_str())
                .ok_or_else(|| {
                    RailReadError::InvalidPlacement("pooled route has no placement owner".into())
                })?;
            let mut lease = pool.acquire(
                ConnectionKey::new(route, owner, self.configuration_generation),
                open,
            )?;
            // Refresh both socket timeouts on every lease, including reused QPs.
            // These remain per-operation TCP timeouts, not a wall-clock deadline.
            lease
                .client()
                .set_io_timeout(timeout)
                .map_err(|error| RailReadError::Transport(error.to_string()))?;
            Ok(lease)
        } else {
            open().map(Lease::unpooled)
        }
    }
}

fn validate_outcome(
    result: anyhow::Result<Option<RdmaReadOutcome>>,
    task: &RailTask,
    descriptor: &pb::ObjectDescriptor,
) -> Result<(), RailReadError> {
    let outcome = result
        .map_err(|error| RailReadError::Transport(error.to_string()))?
        .ok_or(RailReadError::StaleDescriptor)?;
    // Stripe-subset ACKs count selected stripes. Full-descriptor ACKs instead
    // count server WRITE splits/segments; preserve their legacy byte check.
    let incomplete_stripes = descriptor.is_striped && outcome.chunks != task.stripes.len() as u32;
    if outcome.bytes != task.packed_len || incomplete_stripes {
        return Err(RailReadError::Incomplete {
            expected: task.packed_len,
            actual: outcome.bytes,
        });
    }
    Ok(())
}

impl RailTransport for VerbsTransport<'_> {
    fn fetch(
        &self,
        route: &RailRoute,
        task: &RailTask,
        descriptor: &pb::ObjectDescriptor,
        timeout: Duration,
    ) -> Result<Vec<u8>, RailReadError> {
        let timing_start = Instant::now();
        let mut lease = self.acquire(route, timeout)?;
        let connect_us = timing_start.elapsed().as_micros();
        let allocation_start = Instant::now();
        let capacity = task
            .packed_len
            .checked_add(task.dummy_len)
            .ok_or_else(|| RailReadError::ResourceExhausted("rail buffer overflow".into()))?;
        let mut packed = Vec::new();
        packed
            .try_reserve_exact(capacity)
            .map_err(|error| RailReadError::ResourceExhausted(error.to_string()))?;
        packed.resize(capacity, 0u8);
        let allocation_us = allocation_start.elapsed().as_micros();
        let registration_start = Instant::now();
        let registered = lease
            .client()
            .register_buffer(&mut packed)
            .map_err(|error| RailReadError::Transport(error.to_string()))?;
        let registration_us = registration_start.elapsed().as_micros();
        let get_start = Instant::now();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let result = (|| {
                let client = lease.client();
                if descriptor.is_striped {
                    let view = registered.view();
                    let segments = build_sge_segments(task, descriptor, view.addr(), view.rkey())
                        .map_err(|error| anyhow::anyhow!(error.to_string()))?;
                    let indices: Vec<u32> =
                        task.stripes.iter().map(|stripe| stripe.index).collect();
                    client.get_descriptor_stripes_sge_detailed(descriptor, &indices, &segments)
                } else {
                    client.get_descriptor_into_detailed(descriptor, &registered, 0)
                }
            })();
            validate_outcome(result, task, descriptor)
        }));
        let get_us = get_start.elapsed().as_micros();
        let teardown_start = Instant::now();
        finish_transfer(lease, registered, result)?;
        let teardown_us = teardown_start.elapsed().as_micros();
        packed.truncate(task.packed_len);
        if crate::rail_timing_enabled() {
            eprintln!(
                "RAIL_TRANSPORT_TIMING rail={} bytes={} connect_us={connect_us} allocation_us={allocation_us} registration_us={registration_us} get_us={get_us} teardown_us={teardown_us} total_us={}",
                route.id,
                task.packed_len,
                timing_start.elapsed().as_micros(),
            );
        }
        Ok(packed)
    }

    fn fetch_into(
        &self,
        route: &RailRoute,
        task: &RailTask,
        descriptor: &pb::ObjectDescriptor,
        target: &mut [u8],
        timeout: Duration,
    ) -> Result<(), RailReadError> {
        if !descriptor.is_striped || target.len() != task.packed_len || task.dummy_len == 0 {
            return Err(RailReadError::InvalidPlacement(
                "direct Rail target does not match a partial stripe task".into(),
            ));
        }
        let timing_start = Instant::now();
        let mut dummy = Vec::new();
        dummy
            .try_reserve_exact(task.dummy_len)
            .map_err(|error| RailReadError::ResourceExhausted(error.to_string()))?;
        dummy.resize(task.dummy_len, 0);
        let mut lease = self.acquire(route, timeout)?;
        let connect_us = timing_start.elapsed().as_micros();
        let registration_start = Instant::now();
        let registered_target = lease
            .client()
            .register_buffer(target)
            .map_err(|error| RailReadError::Transport(error.to_string()))?;
        let registered_dummy = match lease.client().register_buffer(&mut dummy) {
            Ok(registered) => registered,
            Err(error) => {
                drop(lease);
                drop(registered_target);
                return Err(RailReadError::Transport(error.to_string()));
            }
        };
        let registration_us = registration_start.elapsed().as_micros();
        let get_start = Instant::now();
        let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let result = (|| {
                let target_view = registered_target.view();
                let dummy_view = registered_dummy.view();
                let segments = build_sge_segments_direct(
                    task,
                    descriptor,
                    SgeRegion {
                        addr: target_view.addr(),
                        rkey: target_view.rkey(),
                        len: registered_target.len(),
                    },
                    SgeRegion {
                        addr: dummy_view.addr(),
                        rkey: dummy_view.rkey(),
                        len: registered_dummy.len(),
                    },
                )
                .map_err(|error| anyhow::anyhow!(error.to_string()))?;
                let indices: Vec<u32> = task.stripes.iter().map(|stripe| stripe.index).collect();
                lease
                    .client()
                    .get_descriptor_stripes_sge_detailed(descriptor, &indices, &segments)
            })();
            validate_outcome(result, task, descriptor)
        }));
        let get_us = get_start.elapsed().as_micros();
        let teardown_start = Instant::now();
        finish_transfer(lease, (registered_dummy, registered_target), result)?;
        let teardown_us = teardown_start.elapsed().as_micros();
        if crate::rail_timing_enabled() {
            eprintln!(
                "RAIL_TRANSPORT_DIRECT_TIMING rail={} bytes={} connect_us={connect_us} registration_us={registration_us} get_us={get_us} teardown_us={teardown_us} total_us={}",
                route.id,
                task.packed_len,
                timing_start.elapsed().as_micros(),
            );
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests;
