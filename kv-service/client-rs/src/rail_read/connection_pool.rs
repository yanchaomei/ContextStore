//! Exclusive resource leases for the optional per-reader RDMA connection pool.

use super::RailReadError;
use std::sync::{Arc, Mutex, MutexGuard};

/// Opaque evidence that a transient transport failure has no live QP or MR.
/// Only the resource owner can create this evidence; strings are insufficient.
#[derive(Debug)]
pub struct QuiescentTransportFailure {
    reason: String,
}

impl std::fmt::Display for QuiescentTransportFailure {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.reason.fmt(f)
    }
}

fn transient_control_io(error: &anyhow::Error) -> bool {
    use std::io::ErrorKind;
    error
        .chain()
        .find_map(|cause| cause.downcast_ref::<std::io::Error>())
        .is_some_and(|error| {
            matches!(
                error.kind(),
                ErrorKind::TimedOut
                    | ErrorKind::WouldBlock
                    | ErrorKind::ConnectionReset
                    | ErrorKind::ConnectionAborted
                    | ErrorKind::ConnectionRefused
                    | ErrorKind::BrokenPipe
                    | ErrorKind::UnexpectedEof
            )
        })
}

pub(super) fn connect_for_read(
    config: crate::rdma::RdmaClientConfig,
    recovery: bool,
) -> Result<crate::rdma::RdmaClient, RailReadError> {
    // Connect never registers a target or submits a data transfer. Its failure
    // path destroys the handshake QP before returning to this owner boundary.
    crate::rdma::RdmaClient::connect(config).map_err(|error| {
        if recovery && transient_control_io(&error) {
            RailReadError::QuiescentTransport(QuiescentTransportFailure {
                reason: format!("{error:#}"),
            })
        } else {
            RailReadError::Transport(format!("{error:#}"))
        }
    })
}

pub(super) fn finish_get<K, C, R>(
    lease: Lease<K, C>,
    registrations: R,
    result: std::thread::Result<anyhow::Result<Option<crate::rdma::RdmaReadOutcome>>>,
    task: &super::RailTask,
    descriptor: &crate::pb::ObjectDescriptor,
    recovery: bool,
) -> Result<(), RailReadError> {
    let transient = recovery
        && descriptor.is_striped
        && matches!(&result, Ok(Err(error)) if transient_control_io(error));
    let result = result.map(|result| super::validate_outcome(result, task, descriptor));
    // The cleanup certificate is constructed only AFTER QP destruction and MR
    // deregistration have completed. Panic/protocol/count errors get no proof.
    match finish_transfer(lease, registrations, result) {
        Err(RailReadError::Transport(reason)) if transient => Err(
            RailReadError::QuiescentTransport(QuiescentTransportFailure { reason }),
        ),
        result => result,
    }
}

#[cfg(test)]
pub(super) fn mock_quiescent_failure() -> RailReadError {
    // Explicit private mock seam only, never evidence of provider quiescence.
    RailReadError::QuiescentTransport(QuiescentTransportFailure {
        reason: "mock transient control disconnect after simulated cleanup".into(),
    })
}

/// Bounds established, connecting, idle, and retiring clients in one reader.
#[derive(Clone, Copy, Debug)]
pub struct RailConnectionPoolOptions {
    /// Positive maximum number of live client resource slots.
    pub max_live_connections: usize,
    /// Caller-selected generation of the immutable route configuration.
    pub configuration_generation: u64,
}

/// Cumulative events and an instantaneous, mutually consistent pool snapshot.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct RailConnectionPoolSnapshot {
    /// Successfully established connections, including ones since retired.
    pub created: u64,
    /// Leases taken from the idle pool.
    pub reused: u64,
    /// Established clients destroyed after failure, eviction, or abandonment.
    pub retired: u64,
    /// Fail-fast capacity rejections.
    pub rejected: u64,
    /// All slots: connecting + active + idle + retiring.
    pub live: usize,
    pub connecting: usize,
    pub active: usize,
    pub idle: usize,
    pub retiring: usize,
}

pub(super) struct ConnectionPool<K, C> {
    inner: Arc<PoolInner<K, C>>,
}

struct PoolInner<K, C> {
    max_live: usize,
    state: Mutex<PoolState<K, C>>,
}

struct PoolState<K, C> {
    idle: Vec<(K, C)>,
    counts: RailConnectionPoolSnapshot,
}

impl<K, C> PoolInner<K, C> {
    fn state(&self) -> MutexGuard<'_, PoolState<K, C>> {
        self.state.lock().unwrap_or_else(|error| error.into_inner())
    }

    fn destroy(&self, client: C) {
        // The slot remains charged while provider teardown runs, without a lock.
        drop(client);
        let mut state = self.state();
        state.counts.retiring -= 1;
        state.counts.live -= 1;
        state.counts.retired += 1;
    }
}

impl<K: Eq, C> ConnectionPool<K, C> {
    pub(super) fn new(max_live: usize) -> Self {
        Self {
            inner: Arc::new(PoolInner {
                max_live,
                state: Mutex::new(PoolState {
                    idle: Vec::new(),
                    counts: RailConnectionPoolSnapshot::default(),
                }),
            }),
        }
    }

    pub(super) fn snapshot(&self) -> RailConnectionPoolSnapshot {
        self.inner.state().counts
    }

    pub(super) fn acquire(
        &self,
        key: K,
        open: impl FnOnce() -> Result<C, RailReadError>,
    ) -> Result<Lease<K, C>, RailReadError> {
        let mut state = self.inner.state();
        if let Some(index) = state.idle.iter().position(|(idle_key, _)| idle_key == &key) {
            let (_, client) = state.idle.swap_remove(index);
            state.counts.idle -= 1;
            state.counts.active += 1;
            state.counts.reused += 1;
            return Ok(Lease {
                client: Some(client),
                key: Some(key),
                pool: Some(Arc::clone(&self.inner)),
            });
        }
        if state.counts.live >= self.inner.max_live {
            let Some((_, victim)) = state.idle.pop() else {
                state.counts.rejected += 1;
                return Err(RailReadError::ResourceExhausted(
                    "RDMA connection pool is full".into(),
                ));
            };
            state.counts.idle -= 1;
            state.counts.retiring += 1;
            drop(state);
            drop(victim);
            state = self.inner.state();
            state.counts.retiring -= 1;
            state.counts.retired += 1;
            // Retain this slot throughout teardown; another call cannot steal
            // the capacity reserved for the new route or force an eviction loop.
        } else {
            state.counts.live += 1;
        }
        state.counts.connecting += 1;
        drop(state);
        let reservation = ConnectingSlot(&self.inner);
        let client = open()?;
        let mut state = self.inner.state();
        state.counts.connecting -= 1;
        state.counts.active += 1;
        state.counts.created += 1;
        std::mem::forget(reservation);
        Ok(Lease {
            client: Some(client),
            key: Some(key),
            pool: Some(Arc::clone(&self.inner)),
        })
    }
}

// Also returns capacity if opening a client panics before producing a resource.
struct ConnectingSlot<'a, K, C>(&'a PoolInner<K, C>);

impl<K, C> Drop for ConnectingSlot<'_, K, C> {
    fn drop(&mut self) {
        let mut state = self.0.state();
        state.counts.connecting -= 1;
        state.counts.live -= 1;
    }
}

pub(super) struct Lease<K, C> {
    client: Option<C>,
    key: Option<K>,
    pool: Option<Arc<PoolInner<K, C>>>,
}

impl<K, C> Lease<K, C> {
    pub(super) fn unpooled(client: C) -> Self {
        Self {
            client: Some(client),
            key: None,
            pool: None,
        }
    }

    pub(super) fn client(&mut self) -> &mut C {
        self.client
            .as_mut()
            .expect("lease owns a client until release")
    }

    pub(super) fn release_healthy(mut self) {
        if let Some(pool) = &self.pool {
            let mut state = pool.state();
            let client = self.client.take().expect("live lease");
            let key = self.key.take().expect("pooled lease key");
            state.idle.push((key, client));
            state.counts.active -= 1;
            state.counts.idle += 1;
        }
    }
}

impl<K, C> Drop for Lease<K, C> {
    fn drop(&mut self) {
        if let Some(client) = self.client.take() {
            if let Some(pool) = &self.pool {
                {
                    let mut state = pool.state();
                    state.counts.active -= 1;
                    state.counts.retiring += 1;
                }
                pool.destroy(client);
            } else {
                drop(client);
            }
        }
    }
}

pub(super) fn finish_transfer<K, C, R>(
    lease: Lease<K, C>,
    registrations: R,
    result: std::thread::Result<Result<(), RailReadError>>,
) -> Result<(), RailReadError> {
    // The caller must catch unwinding while the MRs are still in its scope.
    // Lease::drop alone cannot protect MRs declared after the lease: Rust
    // would deregister those first while a remote WRITE could still arrive.
    if matches!(&result, Ok(Ok(()))) && lease.pool.is_some() {
        drop(registrations);
        lease.release_healthy();
    } else {
        // Preserve legacy teardown, and retire every uncertain pooled QP
        // before releasing either target or dummy registration.
        drop(lease);
        drop(registrations);
    }
    match result {
        Ok(result) => result,
        Err(panic) => std::panic::resume_unwind(panic),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct DropProbe(Box<dyn FnMut() + Send>);

    impl Drop for DropProbe {
        fn drop(&mut self) {
            self.0();
        }
    }

    fn probe(events: &Arc<Mutex<Vec<&'static str>>>, name: &'static str) -> DropProbe {
        let events = Arc::clone(events);
        DropProbe(Box::new(move || events.lock().unwrap().push(name)))
    }

    // This private resource seam validates ownership/admission only; it does
    // not stand in for a provider-backed Verbs connection or successful DMA.
    #[test]
    fn healthy_connection_is_reused_exclusively() {
        let pool = ConnectionPool::new(1);
        let opens = AtomicUsize::new(0);
        let open = || Ok(opens.fetch_add(1, Ordering::SeqCst) + 1);
        let mut first = pool.acquire("route-a", open).unwrap();
        assert_eq!(*first.client(), 1);
        first.release_healthy();
        let mut second = pool.acquire("route-a", open).unwrap();
        assert_eq!(*second.client(), 1, "healthy QP must survive the request");
        assert_eq!(opens.load(Ordering::SeqCst), 1);
    }

    #[test]
    fn active_connection_limit_fails_fast_without_opening_another_client() {
        let pool = ConnectionPool::new(1);
        let _first = pool.acquire("route-a", || Ok(1)).unwrap();
        let result = pool.acquire("route-a", || Ok(2));
        assert!(matches!(result, Err(RailReadError::ResourceExhausted(_))));
        assert_eq!(pool.snapshot().created, 1);
        assert_eq!(pool.snapshot().active, 1);
        assert_eq!(pool.snapshot().live, 1);
        assert_eq!(pool.snapshot().rejected, 1);
    }

    #[test]
    fn idle_other_route_is_evicted_before_opening_new_route() {
        let pool = ConnectionPool::new(1);
        pool.acquire("route-a", || Ok(1)).unwrap().release_healthy();
        let mut next = pool.acquire("route-b", || Ok(2)).unwrap();
        assert_eq!(*next.client(), 2);
        assert_eq!(pool.snapshot().created, 2);
        assert_eq!(pool.snapshot().reused, 0);
        assert_eq!(pool.snapshot().retired, 1);
        assert_eq!(pool.snapshot().live, 1);
    }

    #[test]
    fn healthy_transfer_deregisters_mr_before_making_connection_idle() {
        let pool = ConnectionPool::new(1);
        let events = Arc::new(Mutex::new(Vec::new()));
        let lease = pool
            .acquire("route-a", || Ok(probe(&events, "QP")))
            .unwrap();
        finish_transfer(lease, probe(&events, "MR"), Ok(Ok(()))).unwrap();
        assert_eq!(*events.lock().unwrap(), vec!["MR"]);
        assert_eq!(pool.snapshot().idle, 1);
        drop(pool);
        assert_eq!(*events.lock().unwrap(), vec!["MR", "QP"]);
    }

    #[test]
    fn failure_and_panic_destroy_qp_before_both_mrs() {
        for panic in [false, true] {
            let pool = ConnectionPool::new(1);
            let events = Arc::new(Mutex::new(Vec::new()));
            let lease = pool
                .acquire("route-a", || Ok(probe(&events, "QP")))
                .unwrap();
            let registrations = (probe(&events, "dummy MR"), probe(&events, "target MR"));
            let result = std::panic::catch_unwind(|| {
                if panic {
                    panic!("injected transfer panic");
                }
                Err(RailReadError::Transport("injected failed ACK".into()))
            });
            let finished = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                finish_transfer(lease, registrations, result)
            }));
            if panic {
                assert!(finished.is_err());
            } else {
                assert!(finished.unwrap().is_err());
            }
            assert_eq!(*events.lock().unwrap(), vec!["QP", "dummy MR", "target MR"]);
            let snapshot = pool.snapshot();
            assert_eq!(snapshot.live, 0);
            assert_eq!(snapshot.active, 0);
            assert_eq!(snapshot.idle, 0);
            assert_eq!(snapshot.retired, 1);
        }
    }

    #[test]
    fn legacy_success_still_destroys_qp_before_mr() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let lease: Lease<&str, _> = Lease::unpooled(probe(&events, "QP"));
        finish_transfer(lease, probe(&events, "MR"), Ok(Ok(()))).unwrap();
        assert_eq!(*events.lock().unwrap(), vec!["QP", "MR"]);
    }

    #[test]
    fn deregistration_runs_while_lease_is_still_active() {
        let pool = ConnectionPool::new(1);
        let lease = pool.acquire("route-a", || Ok(1)).unwrap();
        let inner = Arc::clone(&pool.inner);
        let registration = DropProbe(Box::new(move || {
            let state = inner.state();
            assert_eq!(state.counts.active, 1);
            assert_eq!(state.counts.idle, 0);
        }));
        finish_transfer(lease, registration, Ok(Ok(()))).unwrap();
        assert_eq!(pool.snapshot().idle, 1);
    }

    #[test]
    fn failed_and_panicking_connect_release_the_reserved_slot() {
        let pool: ConnectionPool<&str, usize> = ConnectionPool::new(1);
        assert!(pool
            .acquire("route-a", || Err(RailReadError::Transport(
                "dial failed".into()
            )))
            .is_err());
        assert_eq!(pool.snapshot().live, 0);
        let result = std::panic::catch_unwind(|| {
            let _ = pool.acquire("route-a", || panic!("dial panicked"));
        });
        assert!(result.is_err());
        assert_eq!(pool.snapshot().live, 0);
        assert_eq!(pool.snapshot().connecting, 0);
        let _next = pool.acquire("route-a", || Ok(1)).unwrap();
        assert_eq!(pool.snapshot().created, 1);
    }

    #[test]
    fn connecting_slot_counts_toward_capacity_without_holding_pool_lock() {
        let pool = ConnectionPool::new(1);
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        std::thread::scope(|scope| {
            let pool_ref = &pool;
            let worker = scope.spawn(move || {
                pool_ref.acquire("route-a", || {
                    started_tx.send(()).unwrap();
                    release_rx
                        .recv_timeout(std::time::Duration::from_secs(5))
                        .unwrap();
                    Ok(1)
                })
            });
            started_rx
                .recv_timeout(std::time::Duration::from_secs(5))
                .unwrap();
            assert_eq!(pool.snapshot().connecting, 1);
            assert_eq!(pool.snapshot().live, 1);
            assert!(pool.acquire("route-b", || Ok(2)).is_err());
            release_tx.send(()).unwrap();
            drop(worker.join().unwrap().unwrap());
        });
        assert_eq!(pool.snapshot().live, 0);
    }

    #[test]
    fn retiring_slot_remains_charged_until_client_destruction_returns() {
        let pool = ConnectionPool::new(1);
        let (started_tx, started_rx) = std::sync::mpsc::channel();
        let (release_tx, release_rx) = std::sync::mpsc::channel();
        let lease = pool
            .acquire("route-a", || {
                Ok(DropProbe(Box::new(move || {
                    started_tx.send(()).unwrap();
                    release_rx
                        .recv_timeout(std::time::Duration::from_secs(5))
                        .unwrap();
                })))
            })
            .unwrap();
        std::thread::scope(|scope| {
            let worker = scope.spawn(move || drop(lease));
            started_rx
                .recv_timeout(std::time::Duration::from_secs(5))
                .unwrap();
            let snapshot = pool.snapshot();
            assert_eq!(snapshot.live, 1);
            assert_eq!(snapshot.retiring, 1);
            assert_eq!(snapshot.active, 0);
            assert_eq!(snapshot.retired, 0);
            assert!(pool
                .acquire("route-b", || Ok(DropProbe(Box::new(|| {}))))
                .is_err());
            release_tx.send(()).unwrap();
            worker.join().unwrap();
        });
        assert_eq!(pool.snapshot().live, 0);
        assert_eq!(pool.snapshot().retired, 1);
    }

    #[test]
    fn concurrent_acquisition_never_exceeds_reader_bound() {
        let pool = ConnectionPool::new(3);
        let acquired = std::sync::Barrier::new(9);
        let release = std::sync::Barrier::new(9);
        std::thread::scope(|scope| {
            let pool = &pool;
            let acquired = &acquired;
            let release = &release;
            let workers: Vec<_> = (0..8)
                .map(|id| {
                    scope.spawn(move || {
                        let lease = pool.acquire("route-a", || Ok(id));
                        acquired.wait();
                        release.wait();
                        drop(lease);
                    })
                })
                .collect();
            acquired.wait();
            assert_eq!(pool.snapshot().active, 3);
            assert_eq!(pool.snapshot().live, 3);
            assert_eq!(pool.snapshot().rejected, 5);
            release.wait();
            for worker in workers {
                worker.join().unwrap();
            }
        });
        assert_eq!(pool.snapshot().live, 0);
    }

    #[test]
    fn dropping_reader_pool_drains_all_idle_clients() {
        let events = Arc::new(Mutex::new(Vec::new()));
        let pool = ConnectionPool::new(2);
        let first = pool
            .acquire("route-a", || Ok(probe(&events, "QP a")))
            .unwrap();
        let second = pool
            .acquire("route-b", || Ok(probe(&events, "QP b")))
            .unwrap();
        first.release_healthy();
        second.release_healthy();
        assert_eq!(pool.snapshot().idle, 2);
        assert!(events.lock().unwrap().is_empty());
        drop(pool);
        assert_eq!(events.lock().unwrap().len(), 2);
    }
    #[test]
    fn only_whitelisted_structured_control_io_is_certified_after_cleanup() {
        use std::io::ErrorKind;
        let descriptor = crate::pb::ObjectDescriptor {
            is_striped: true,
            ..Default::default()
        };
        let task = super::super::RailTask {
            route_index: 0,
            packed_len: 8,
            dummy_len: 8,
            stripes: vec![super::super::StripeRead {
                index: 0,
                object_offset: 0,
                packed_offset: 0,
                length: 8,
            }],
        };
        for kind in [
            ErrorKind::TimedOut,
            ErrorKind::WouldBlock,
            ErrorKind::ConnectionReset,
            ErrorKind::ConnectionAborted,
            ErrorKind::ConnectionRefused,
            ErrorKind::BrokenPipe,
            ErrorKind::UnexpectedEof,
            ErrorKind::InvalidData,
            ErrorKind::PermissionDenied,
            ErrorKind::OutOfMemory,
        ] {
            let events = Arc::new(Mutex::new(Vec::new()));
            let lease: Lease<&str, _> = Lease::unpooled(probe(&events, "QP"));
            let raw = anyhow::Error::new(std::io::Error::new(kind, "injected control I/O"))
                .context("GET control channel");
            let result = finish_get(
                lease,
                probe(&events, "MR"),
                Ok(Err(raw)),
                &task,
                &descriptor,
                true,
            );
            assert_eq!(*events.lock().unwrap(), vec!["QP", "MR"]);
            let allowed = !matches!(
                kind,
                ErrorKind::InvalidData | ErrorKind::PermissionDenied | ErrorKind::OutOfMemory
            );
            assert_eq!(
                matches!(result, Err(RailReadError::QuiescentTransport(_))),
                allowed
            );
        }
        let events = Arc::new(Mutex::new(Vec::new()));
        let lease: Lease<&str, _> = Lease::unpooled(probe(&events, "QP"));
        let result = finish_get(
            lease,
            probe(&events, "MR"),
            Ok(Err(anyhow::anyhow!("ConnectionReset"))),
            &task,
            &descriptor,
            true,
        );
        assert!(
            matches!(result, Err(RailReadError::Transport(_))),
            "text never certifies a failure"
        );
    }

    #[test]
    fn recovery_certificate_is_not_returned_while_qp_or_mr_destruction_is_blocked() {
        use std::sync::mpsc;
        for block_mr in [false, true] {
            let (entered_tx, entered_rx) = mpsc::channel();
            let (release_tx, release_rx) = mpsc::channel();
            let (result_tx, result_rx) = mpsc::channel();
            let blocking = DropProbe(Box::new(move || {
                entered_tx.send(()).unwrap();
                release_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .unwrap();
            }));
            let idle = DropProbe(Box::new(|| {}));
            let (client, registration) = if block_mr {
                (idle, blocking)
            } else {
                (blocking, idle)
            };
            std::thread::scope(|scope| {
                scope.spawn(move || {
                    let lease: Lease<&str, _> = Lease::unpooled(client);
                    let descriptor = crate::pb::ObjectDescriptor {
                        is_striped: true,
                        ..Default::default()
                    };
                    let task = super::super::RailTask {
                        route_index: 0,
                        packed_len: 8,
                        dummy_len: 8,
                        stripes: vec![],
                    };
                    let raw = anyhow::Error::new(std::io::Error::new(
                        std::io::ErrorKind::ConnectionReset,
                        "mock TCP",
                    ));
                    result_tx
                        .send(finish_get(
                            lease,
                            registration,
                            Ok(Err(raw)),
                            &task,
                            &descriptor,
                            true,
                        ))
                        .unwrap();
                });
                entered_rx
                    .recv_timeout(std::time::Duration::from_secs(5))
                    .unwrap();
                assert!(matches!(
                    result_rx.try_recv(),
                    Err(mpsc::TryRecvError::Empty)
                ));
                release_tx.send(()).unwrap();
                assert!(matches!(
                    result_rx
                        .recv_timeout(std::time::Duration::from_secs(5))
                        .unwrap(),
                    Err(RailReadError::QuiescentTransport(_))
                ));
            });
        }
    }
}
