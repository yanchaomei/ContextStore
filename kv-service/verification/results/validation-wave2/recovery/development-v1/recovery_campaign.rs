//! Hardware-gated recovery controls. Production F2 is always called through KvClient.
//!
//! The F0/F1 staging orchestration below is an intentional frozen copy of
//! rail_read.rs at 1c3ee135. It shares the real planner, owner-certified Verbs
//! transport, admission guard, checksums and publication primitive. The only
//! policy difference is fail without retry (F0), or discard successful task
//! buffers and re-read every frozen task once after all initial joins (F1).
//! This copy requires parity review before drawing a timing comparison.

use super::super::*;
use crate::KvClient;
use prost::bytes::Bytes;
use std::io::{Read, Write};
use std::net::{Shutdown, TcpListener};
use std::sync::Condvar;
use std::thread::JoinHandle;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Control {
    F0,
    F1,
    F2,
}

impl RailReader {
    fn control_retry_tasks<T>(
        &self,
        plan: &RailPlan,
        control: Control,
        request: ReadRequest<'_>,
        mut results: Vec<Result<T, RailReadError>>,
        budget: &mut BudgetGuard,
        mut fetch: impl FnMut(&RailTask, Duration) -> Result<T, RailReadError>,
    ) -> Result<Vec<T>, RailReadError> {
        check_live(request.cancel, request.deadline)?;
        let failed: HashSet<usize> = plan
            .tasks
            .iter()
            .zip(&results)
            .filter(|(_, result)| result.is_err())
            .map(|(task, _)| task.route_index)
            .collect();
        if failed.is_empty() {
            return results.into_iter().collect();
        }
        let rejected = results.iter().position(|result| match result {
            Err(RailReadError::QuiescentTransport(_)) => {
                control == Control::F0 || !request.descriptor.is_striped
            }
            Err(_) => true,
            Ok(_) => false,
        });
        if let Some(index) = rejected {
            return Err(results.swap_remove(index).err().unwrap());
        }
        // Destroy every successful packed task before replacement allocation.
        // Direct staging uses unit results and reuses its single private object.
        results.clear();
        budget.release_rails();
        for task in &plan.tasks {
            check_live(request.cancel, request.deadline)?;
            let endpoint = &self.routes[task.route_index].advertised_endpoint;
            let mut owners = request
                .placement
                .chunks
                .iter()
                .filter(|chunk| &chunk.rdma_endpoint == endpoint)
                .map(|chunk| chunk.node_id.as_str());
            let owner = owners.next().filter(|owner| !owner.trim().is_empty());
            let unambiguous = owner.is_some_and(|owner| owners.all(|other| other == owner));
            let route_index = self
                .routes
                .iter()
                .enumerate()
                .find(|(index, route)| {
                    unambiguous
                        && !failed.contains(index)
                        && &route.advertised_endpoint == endpoint
                        && self.counters[*index].is_available()
                })
                .map(|(index, _)| index)
                .ok_or_else(|| {
                    RailReadError::InvalidPlacement(
                        "whole-read reference has no healthy same-owner path".into(),
                    )
                })?;
            let mut retry = task.clone();
            retry.route_index = route_index;
            budget.reserve_retry(&retry, &self.limits)?;
            let result = self.attempt(&retry, request.cancel, request.deadline, |timeout| {
                self.recovery_counts[0].fetch_add(1, Ordering::Relaxed);
                self.recovery_counts[1].fetch_add(retry.packed_len as u64, Ordering::Relaxed);
                let result = fetch(&retry, timeout);
                if result.is_ok() {
                    self.recovery_counts[2].fetch_add(1, Ordering::Relaxed);
                }
                result
            });
            budget.release_rails();
            results.push(Ok(result?));
        }
        results.into_iter().collect()
    }
    fn control_fetch_direct_staged<T: RailTransport>(
        &self,
        plan: &RailPlan,
        control: Control,
        request: ReadRequest<'_>,
        transport: &T,
        budget: &mut BudgetGuard,
    ) -> Result<(Vec<u8>, u128), RailReadError> {
        let ReadRequest {
            descriptor,
            cancel,
            deadline,
            ..
        } = request;
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
                    scope.spawn(move || {
                        self.attempt(task, cancel, deadline, |timeout| {
                            transport.fetch_into(
                                &self.routes[task.route_index],
                                task,
                                descriptor,
                                target,
                                timeout,
                            )
                        })
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or(Err(RailReadError::WorkerPanic)))
                .collect::<Vec<_>>()
        });
        // All old mutable borrows have ended. Check every successful task before
        // spending recovery bytes; partial failed ranges are never Ready.
        if self.recovery.is_some() && results.iter().any(Result::is_err) {
            for (task, result) in plan.tasks.iter().zip(&results) {
                if result.is_ok() {
                    let offset = task.stripes[0].object_offset;
                    self.validate_task(plan, task, &staged[offset..offset + task.packed_len])?;
                }
            }
        }
        self.control_retry_tasks(plan, control, request, results, budget, |task, timeout| {
            let offset = task.stripes[0].object_offset;
            let target = &mut staged[offset..offset + task.packed_len];
            transport.fetch_into(
                &self.routes[task.route_index],
                task,
                descriptor,
                target,
                timeout,
            )?;
            self.validate_task(plan, task, target)
        })?;
        Ok((staged, workers_start.elapsed().as_micros()))
    }

    fn control_read_staged<T: RailTransport>(
        &self,
        control: Control,
        descriptor: &pb::ObjectDescriptor,
        placement: &pb::PlacementDescriptor,
        transport: &T,
        cancel: Option<&RailCancel>,
        deadline: Option<Instant>,
    ) -> Result<StagedRailRead, RailReadError> {
        check_live(cancel, deadline)?;
        let request = ReadRequest {
            descriptor,
            placement,
            cancel,
            deadline,
        };
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
                self.control_fetch_direct_staged(&plan, control, request, transport, &mut budget)?;
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
            check_live(cancel, deadline)?;
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
                deadline,
            });
        }
        let workers_start = Instant::now();
        let results = std::thread::scope(|scope| {
            let handles: Vec<_> = plan
                .tasks
                .iter()
                .map(|task| {
                    scope.spawn(move || {
                        self.attempt(task, cancel, deadline, |timeout| {
                            transport.fetch(
                                &self.routes[task.route_index],
                                task,
                                descriptor,
                                timeout,
                            )
                        })
                    })
                })
                .collect();
            handles
                .into_iter()
                .map(|handle| handle.join().unwrap_or(Err(RailReadError::WorkerPanic)))
                .collect::<Vec<_>>()
        });
        if self.recovery.is_some() && descriptor.is_striped && results.iter().any(Result::is_err) {
            for (task, result) in plan.tasks.iter().zip(&results) {
                if let Ok(packed) = result {
                    self.validate_task(&plan, task, packed)?;
                }
            }
        }
        let results = self.control_retry_tasks(
            &plan,
            control,
            request,
            results,
            &mut budget,
            |task, timeout| {
                let packed =
                    transport.fetch(&self.routes[task.route_index], task, descriptor, timeout)?;
                self.validate_task(&plan, task, &packed)?;
                Ok(packed)
            },
        )?;
        let workers_us = workers_start.elapsed().as_micros();
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
            let packed = results.into_iter().next().expect("one planned task");
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
            for (task, packed) in plan.tasks.iter().zip(results) {
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
        check_live(cancel, deadline)?;
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
            deadline,
        })
    }
}

#[derive(Debug)]
struct RecoveryFrame {
    wire: Vec<u8>,
    stripes: Vec<u32>,
    segments: Vec<(u64, u32, u64)>,
    captured: Instant,
}

fn receive_recovery_frame(
    stream: &mut std::net::TcpStream,
) -> std::io::Result<Option<RecoveryFrame>> {
    use std::io::{Error, ErrorKind, Read};
    fn take(
        stream: &mut std::net::TcpStream,
        wire: &mut Vec<u8>,
        count: usize,
    ) -> std::io::Result<()> {
        let offset = wire.len();
        wire.resize(offset + count, 0);
        stream.read_exact(&mut wire[offset..])
    }
    fn string(stream: &mut std::net::TcpStream, wire: &mut Vec<u8>) -> std::io::Result<()> {
        let offset = wire.len();
        take(stream, wire, 2)?;
        let length = u16::from_le_bytes(wire[offset..offset + 2].try_into().unwrap()) as usize;
        take(stream, wire, length)
    }
    let mut tag = [0];
    if stream.read(&mut tag)? == 0 || tag[0] == 99 {
        // Legacy per-request clients send BYE after successful QP teardown.
        return Ok(None);
    }
    if tag[0] != 15 {
        return Err(Error::new(
            ErrorKind::InvalidData,
            "relay requires descriptor-stripe-SGE tag 15",
        ));
    }
    let mut wire = vec![tag[0]];
    string(stream, &mut wire)?; // Canonical key.
    string(stream, &mut wire)?; // Object handle.
    take(stream, &mut wire, 8)?; // Generation.
    string(stream, &mut wire)?; // Content etag.
    take(stream, &mut wire, 49)?; // Layout, size, striping, and legacy destination.
    let offset = wire.len();
    take(stream, &mut wire, 2)?;
    let count = u16::from_le_bytes(wire[offset..offset + 2].try_into().unwrap()) as usize;
    let mut stripes = Vec::with_capacity(count);
    for _ in 0..count {
        let offset = wire.len();
        take(stream, &mut wire, 4)?;
        stripes.push(u32::from_le_bytes(
            wire[offset..offset + 4].try_into().unwrap(),
        ));
    }
    let offset = wire.len();
    take(stream, &mut wire, 2)?;
    let count = u16::from_le_bytes(wire[offset..offset + 2].try_into().unwrap()) as usize;
    let mut segments = Vec::with_capacity(count);
    for _ in 0..count {
        let offset = wire.len();
        take(stream, &mut wire, 20)?;
        segments.push((
            u64::from_le_bytes(wire[offset..offset + 8].try_into().unwrap()),
            u32::from_le_bytes(wire[offset + 8..offset + 12].try_into().unwrap()),
            u64::from_le_bytes(wire[offset + 12..offset + 20].try_into().unwrap()),
        ));
    }
    Ok(Some(RecoveryFrame {
        wire,
        stripes,
        segments,
        captured: Instant::now(),
    }))
}

fn recovery_relay_handshake(
    downstream: &mut std::net::TcpStream,
    endpoint: &str,
) -> std::io::Result<(std::net::TcpStream, u32)> {
    use std::io::{Read, Write};
    let mut upstream = std::net::TcpStream::connect(endpoint)?;
    for stream in [&*downstream, &upstream] {
        stream.set_nodelay(true)?;
        stream.set_read_timeout(Some(Duration::from_secs(90)))?;
        stream.set_write_timeout(Some(Duration::from_secs(90)))?;
    }
    let mut hello = [0; 25];
    downstream.read_exact(&mut hello)?;
    assert_eq!(hello[0], 1);
    let qpn = u32::from_le_bytes(hello[1..5].try_into().unwrap());
    upstream.write_all(&hello)?;
    upstream.read_exact(&mut hello)?;
    assert_eq!(hello[0], 1);
    downstream.write_all(&hello)?;
    Ok((upstream, qpn))
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Cut {
    LateEof,
    AckTimeout,
    CancelInitial,
    CancelRetry,
    DeadlineRetryGate,
    Overwrite,
    Delete,
}

#[derive(Default)]
struct OperationState {
    retry_registered: bool,
    old_terminal: bool,
    held_retry_ack: bool,
    release_retry_ack: bool,
    frames: Vec<String>,
    counts: [usize; 2],
    captures: Vec<(usize, usize, Vec<u32>, Vec<(u64, u32, u64)>, Instant)>,
}

struct Operation {
    id: usize,
    control: Control,
    failed_lane: usize,
    fault: bool,
    cut: Cut,
    cancel: Option<RailCancel>,
    state: Mutex<OperationState>,
    changed: Condvar,
}

impl Operation {
    fn wait(&self, predicate: impl Fn(&OperationState) -> bool) -> anyhow::Result<()> {
        let state = self.state.lock().unwrap();
        let (state, timeout) = self
            .changed
            .wait_timeout_while(state, Duration::from_secs(40), |s| !predicate(s))
            .unwrap();
        anyhow::ensure!(
            !timeout.timed_out() && predicate(&state),
            "relay evidence gate timed out"
        );
        Ok(())
    }

    fn record(
        &self,
        lane: usize,
        sequence: usize,
        frame: &RecoveryFrame,
        peer: &str,
        qpn: u32,
        terminal: &str,
    ) {
        let mut state = self.state.lock().unwrap();
        state.captures.push((
            lane,
            sequence,
            frame.stripes.clone(),
            frame.segments.clone(),
            frame.captured,
        ));
        state.frames.push(format!(
            "RECOVERY_FRAME op={} control={:?} lane={lane} sequence={sequence} peer={peer} qpn={qpn} stripes={:?} segments={:?} terminal={terminal} wire={:02x?}",
            self.id, self.control, frame.stripes, frame.segments, frame.wire));
        self.changed.notify_all();
    }
}

struct Relay {
    endpoint: String,
    current: Arc<Mutex<Option<Arc<Operation>>>>,
    stop: Arc<AtomicBool>,
    thread: Option<JoinHandle<anyhow::Result<()>>>,
}

impl Relay {
    fn start(lane: usize, real: String) -> Self {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        listener.set_nonblocking(true).unwrap();
        let endpoint = listener.local_addr().unwrap().to_string();
        let current = Arc::new(Mutex::new(None::<Arc<Operation>>));
        let active = Arc::clone(&current);
        let stop = Arc::new(AtomicBool::new(false));
        let stopping = Arc::clone(&stop);
        let thread = std::thread::spawn(move || -> anyhow::Result<()> {
            let mut workers = Vec::new();
            while !stopping.load(Ordering::Acquire) {
                match listener.accept() {
                    Ok((mut downstream, _)) => {
                        let active = Arc::clone(&active);
                        let real = real.clone();
                        workers.push(std::thread::spawn(move || -> anyhow::Result<()> {
                            let (mut upstream, qpn) = recovery_relay_handshake(&mut downstream, &real)?;
                            let peer = upstream.local_addr()?.to_string();
                            while let Some(frame) = receive_recovery_frame(&mut downstream)? {
                                let op = active.lock().unwrap().clone().expect("armed operation");
                                let sequence = {
                                    let mut state = op.state.lock().unwrap();
                                    state.counts[lane] += 1;
                                    state.counts[lane]
                                };
                                if op.fault && lane == op.failed_lane {
                                    anyhow::ensure!(sequence == 1, "failed path was retried without explicit rearm");
                                    if matches!(op.cut, Cut::AckTimeout | Cut::DeadlineRetryGate) {
                                        upstream.write_all(&frame.wire)?;
                                        let mut reply = [0; 14];
                                        upstream.read_exact(&mut reply)?;
                                        anyhow::ensure!(reply[0] == 3 && reply[1] == 1, "held ACK requires completed server WRITE");
                                        op.record(lane, sequence, &frame, &peer, qpn, "success_ack_held_until_client_timeout");
                                        op.wait(|state| state.retry_registered)?;
                                        downstream.shutdown(Shutdown::Both)?;
                                    } else {
                                        downstream.shutdown(Shutdown::Both)?;
                                        // F0 signals after fail-fast returns. F1/F2 signal only
                                        // after the complete retry frame proves MR registration.
                                        op.wait(|state| state.retry_registered)?;
                                        upstream.write_all(&frame.wire)?;
                                        let mut byte = [0];
                                        match upstream.read(&mut byte) {
                                            Ok(0) => (),
                                            other => anyhow::bail!("INCONCLUSIVE: expected exact old-server EOF, observed {other:?}"),
                                        }
                                        op.record(lane, sequence, &frame, &peer, qpn, "eof_after_late_request");
                                    }
                                    let mut state = op.state.lock().unwrap();
                                    state.old_terminal = true;
                                    op.changed.notify_all();
                                    return Ok(());
                                }
                                if op.fault && sequence == 2 {
                                    if op.cut == Cut::CancelRetry { op.cancel.as_ref().unwrap().cancel(); }
                                    {
                                        let mut state = op.state.lock().unwrap();
                                        state.retry_registered = true;
                                        op.changed.notify_all();
                                    }
                                    op.wait(|state| state.old_terminal)?;
                                }
                                upstream.write_all(&frame.wire)?;
                                let mut reply = [0; 14];
                                upstream.read_exact(&mut reply)?;
                                anyhow::ensure!(reply[0] == 3 && reply[1] == 1, "healthy read lacked success ACK: {reply:?}");
                                if op.cut == Cut::CancelInitial && lane == op.failed_lane {
                                    op.cancel.as_ref().unwrap().cancel();
                                }
                                if matches!(op.cut, Cut::Overwrite | Cut::Delete) && sequence == 2 {
                                    {
                                        let mut state = op.state.lock().unwrap();
                                        state.held_retry_ack = true;
                                        op.changed.notify_all();
                                    }
                                    op.wait(|state| state.release_retry_ack)?;
                                }
                                downstream.write_all(&reply)?;
                                op.record(lane, sequence, &frame, &peer, qpn, "success_ack");
                            }
                            Ok(())
                        }));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        std::thread::sleep(Duration::from_millis(1))
                    }
                    Err(error) => return Err(error.into()),
                }
                // Reap completed workers so the repeated campaign is bounded.
                let mut index = 0;
                while index < workers.len() {
                    if workers[index].is_finished() {
                        workers
                            .swap_remove(index)
                            .join()
                            .expect("relay worker panic")?;
                    } else {
                        index += 1;
                    }
                }
            }
            for worker in workers {
                worker.join().expect("relay worker panic")?;
            }
            Ok(())
        });
        Self {
            endpoint,
            current,
            stop,
            thread: Some(thread),
        }
    }

    fn arm(&self, op: &Arc<Operation>) {
        *self.current.lock().unwrap() = Some(Arc::clone(op));
    }

    fn finish(mut self) {
        self.stop.store(true, Ordering::Release);
        self.thread
            .take()
            .unwrap()
            .join()
            .expect("relay listener panic")
            .expect("relay evidence");
    }
}

fn setting(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn make_reader(advertised: &str, relays: &[Relay; 2], direct: bool, pooled: bool) -> RailReader {
    let routes = (0..2)
        .map(|index| {
            RailRoute::new(
                format!("rail{index}"),
                advertised,
                RdmaClientConfig::new(
                    &relays[index].endpoint,
                    setting(&format!("CS_RAIL_DEVICE{index}"), &format!("rxe{index}")),
                )
                .with_gid_index(
                    setting(&format!("CS_RAIL_GID{index}"), "1")
                        .parse()
                        .unwrap(),
                ),
            )
        })
        .collect();
    let mut reader = RailReader::new(
        routes,
        RailLimits {
            direct_staging: direct,
            io_timeout: Duration::from_secs(8),
            ..RailLimits::default()
        },
    )
    .unwrap()
    .with_recovery(RailRecoveryOptions {
        timeout: Duration::from_secs(30),
    })
    .unwrap();
    if pooled {
        reader = reader
            .with_connection_pool(RailConnectionPoolOptions {
                max_live_connections: 2,
                configuration_generation: 1,
            })
            .unwrap();
    }
    reader
}

async fn control_read(
    client: &mut KvClient,
    reader: Arc<RailReader>,
    control: Control,
    key: &str,
    destination: &mut [u8],
) -> anyhow::Result<Option<usize>> {
    if control == Control::F2 {
        return client
            .read_multi_rail_into(reader, "recovery-campaign", key, destination, None)
            .await;
    }
    // This outer reference matches KvClient's absolute deadline and both
    // authoritative lookups; it never accepts a newly discovered descriptor.
    let deadline = reader.start_deadline()?;
    let initial = client
        .lookup_object("recovery-campaign", key)
        .await?
        .unwrap();
    let descriptor = initial.descriptor.clone();
    let placement = initial.placement.clone().unwrap();
    let size = usize::try_from(descriptor.size)?;
    anyhow::ensure!(destination.len() >= size, "caller buffer too short");
    let payload = tokio::task::spawn_blocking(move || {
        let transport = VerbsTransport::new(&reader, &placement, &descriptor)?;
        reader.control_read_staged(control, &descriptor, &placement, &transport, None, deadline)
    })
    .await??;
    payload.ensure_live(None)?;
    let current = client
        .lookup_object("recovery-campaign", key)
        .await?
        .ok_or(RailReadError::VersionChanged)?;
    Ok(Some(commit_if_unchanged_until(
        &initial,
        &current,
        payload.as_bytes(),
        destination,
        None,
        payload.deadline,
    )?))
}

fn assert_quiescent(reader: &RailReader) {
    let budget = reader.budget.lock().unwrap();
    assert_eq!(budget.active_reads, 0);
    assert_eq!(budget.staging_bytes, 0);
    assert_eq!(budget.registered_bytes, 0);
    assert_eq!(budget.inflight_bytes, 0);
    assert!(budget.rail_active_reads.iter().all(|count| *count == 0));
    assert!(budget.rail_inflight_bytes.iter().all(|count| *count == 0));
    if let Some(pool) = reader.connection_pool_snapshot() {
        assert_eq!(pool.active, 0);
        assert_eq!(pool.retiring, 0);
        assert!(pool.idle <= 2);
    }
}

#[tokio::test]
#[ignore = "requires isolated native RXE fixture; frozen controls and bounded repeated recovery"]
async fn native_recovery_control_campaign() {
    let total: usize = setting("CS_RECOVERY_OPS", "12").parse().unwrap();
    let seed: u64 = setting("CS_RECOVERY_SEED", "2026102901").parse().unwrap();
    let controls = setting("CS_RECOVERY_CONTROLS", "F0,F1,F2");
    let selected: Vec<Control> = controls
        .split(',')
        .map(|s| match s {
            "F0" => Control::F0,
            "F1" => Control::F1,
            "F2" => Control::F2,
            _ => panic!("unknown control"),
        })
        .collect();
    assert_eq!(
        total % (4 * selected.len()),
        0,
        "equal counts for all modes and controls"
    );
    let mut client = KvClient::connect(setting("CS_RAIL_COORDINATOR", "http://10.81.1.1:25451"))
        .await
        .unwrap();
    let mib: usize = setting("CS_RAIL_OBJECT_MIB", "1").parse().unwrap();
    let payload: Vec<u8> = (0..mib * 1024 * 1024)
        .map(|offset| ((offset as u64 * 17 + seed) % 251) as u8)
        .collect();
    let payloads: Vec<Vec<u8>> = (0u8..4)
        .map(|slot| {
            payload
                .iter()
                .map(|byte| byte.wrapping_add(slot * 41))
                .collect()
        })
        .collect();
    let keys: Vec<String> = (0..4)
        .map(|slot| format!("native-recovery-{seed}-{slot}"))
        .collect();
    let mut initials = Vec::new();
    for (key, payload) in keys.iter().zip(&payloads) {
        client
            .put_stream_chunks("recovery-campaign", key, vec![Bytes::from(payload.clone())])
            .await
            .unwrap();
        initials.push(
            client
                .lookup_object("recovery-campaign", key)
                .await
                .unwrap()
                .unwrap(),
        );
    }
    let initial = &initials[0];
    assert!(initial.descriptor.is_striped && initial.descriptor.stripe_count >= 4);
    let placement = initial.placement.as_ref().unwrap();
    let advertised = &placement.chunks[0].rdma_endpoint;
    let mut op_id = 0;
    for direct in [false, true] {
        for pooled in [false, true] {
            for &control in &selected {
                let relays = [
                    Relay::start(0, setting("CS_RAIL_LISTENER0", "10.81.1.1:25453")),
                    Relay::start(1, setting("CS_RAIL_LISTENER1", "10.82.1.1:25454")),
                ];
                let reader = Arc::new(make_reader(advertised, &relays, direct, pooled));
                let assignment = reader
                    .describe_assignment(&initial.descriptor, placement)
                    .unwrap();
                let mut destination = vec![0xA5; payload.len()];
                for local in 0..total / (4 * selected.len()) {
                    let slot = (local + seed as usize) % payloads.len();
                    let payload = &payloads[slot];
                    let key = &keys[slot];
                    let initial = &initials[slot];
                    let failed_lane = ((local as u64 + seed) % 2) as usize;
                    reader.set_enabled("rail0", true);
                    reader.set_enabled("rail1", true);
                    let before = reader.recovery_snapshot();
                    let op = Arc::new(Operation {
                        id: op_id,
                        control,
                        failed_lane,
                        fault: true,
                        cut: Cut::LateEof,
                        cancel: None,
                        state: Mutex::new(OperationState::default()),
                        changed: Condvar::new(),
                    });
                    for relay in &relays {
                        relay.arm(&op);
                    }
                    destination.fill(0xA5);
                    let start = Instant::now();
                    let result = control_read(
                        &mut client,
                        Arc::clone(&reader),
                        control,
                        &key,
                        &mut destination,
                    )
                    .await;
                    if control == Control::F0 {
                        let error = result.unwrap_err();
                        assert!(
                            matches!(
                                error.downcast_ref::<RailReadError>(),
                                Some(RailReadError::QuiescentTransport(_))
                            ),
                            "F0 must return owner-certified failure: {error:?}"
                        );
                        assert!(destination.iter().all(|byte| *byte == 0xA5));
                        let mut state = op.state.lock().unwrap();
                        state.retry_registered = true;
                        op.changed.notify_all();
                    } else {
                        assert_eq!(result.unwrap(), Some(payload.len()));
                        assert_eq!(&destination, payload);
                    }
                    op.wait(|state| state.old_terminal).unwrap();
                    let after = reader.recovery_snapshot();
                    let expected_retry = match control {
                        Control::F0 => 0,
                        Control::F1 => payload.len() as u64,
                        Control::F2 => assignment[failed_lane].bytes as u64,
                    };
                    assert_eq!(
                        after.requested_payload_bytes - before.requested_payload_bytes,
                        expected_retry
                    );
                    assert_eq!(
                        after.retry_tasks - before.retry_tasks,
                        match control {
                            Control::F0 => 0,
                            Control::F1 => 2,
                            Control::F2 => 1,
                        }
                    );
                    assert_quiescent(&reader);
                    let current = client
                        .lookup_object("recovery-campaign", &key)
                        .await
                        .unwrap()
                        .unwrap();
                    assert_eq!(initial.descriptor, current.descriptor);
                    assert_eq!(initial.placement, current.placement);
                    let state = op.state.lock().unwrap();
                    let old = state
                        .captures
                        .iter()
                        .find(|entry| entry.0 == failed_lane)
                        .unwrap();
                    assert_eq!(old.2, assignment[failed_lane].stripe_indices);
                    let healthy = state
                        .captures
                        .iter()
                        .find(|entry| entry.0 != failed_lane && entry.1 == 1)
                        .unwrap();
                    assert_eq!(healthy.2, assignment[1 - failed_lane].stripe_indices);
                    if control == Control::F2 {
                        let retry = state
                            .captures
                            .iter()
                            .find(|entry| entry.0 != failed_lane && entry.1 == 2)
                            .unwrap();
                        assert_eq!(retry.2, assignment[failed_lane].stripe_indices);
                        if direct {
                            for stripe in &old.2 {
                                assert_eq!(
                                    old.3[*stripe as usize].0, retry.3[*stripe as usize].0,
                                    "direct retry must reuse exactly the retired private range"
                                );
                            }
                        }
                    }
                    for line in &state.frames {
                        eprintln!("{line}");
                    }
                    eprintln!("RECOVERY_REQUEST op={op_id} seed={seed} control={control:?} direct={direct} pooled={pooled} key={key} generation={} expected_hash={:016x} failed_lane={failed_lane} manual_rearm=true expected_retry={expected_retry} observed_retry={} terminal={} elapsed_us={} old_terminal=true frames={} budget_quiescent=true pool={:?}",
                    initial.descriptor.object_generation, twox_hash::xxh3::hash64(&payload),
                    after.requested_payload_bytes - before.requested_payload_bytes,
                    if control == Control::F0 { "expected_error" } else { "exact_publication" }, start.elapsed().as_micros(), state.frames.len(), reader.connection_pool_snapshot());
                    op_id += 1;
                }
                drop(reader);
                for relay in relays {
                    relay.finish();
                }
            }
        }
    }
    assert_eq!(op_id, total);
    eprintln!("RECOVERY_CAMPAIGN_COMPLETE operations={total} seed={seed} controls={controls} incorrect_publications=0 unexpected_errors=0 resource_overshoots=0 old_terminal_missing=0 timing_comparison=withheld_pending_parity_review");
}

#[tokio::test]
#[ignore = "requires isolated RXE; real transfer/ACK/cancel/version cut-point campaign"]
async fn native_recovery_cut_campaign() {
    let repeats: usize = setting("CS_RECOVERY_CUT_REPEATS", "1").parse().unwrap();
    let seed: u64 = setting("CS_RECOVERY_SEED", "2026102903").parse().unwrap();
    let mut client = KvClient::connect(setting("CS_RAIL_COORDINATOR", "http://10.81.1.1:25451"))
        .await
        .unwrap();
    let mib: usize = setting("CS_RAIL_OBJECT_MIB", "1").parse().unwrap();
    let payload: Vec<u8> = (0..mib * 1024 * 1024)
        .map(|offset| ((offset as u64 * 17 + seed) % 251) as u8)
        .collect();
    let version_b: Vec<u8> = payload.iter().map(|byte| byte.wrapping_add(73)).collect();
    assert_ne!(payload, version_b);
    let mut op_id = 0;
    for direct in [false, true] {
        for pooled in [false, true] {
            for cut in [
                Cut::AckTimeout,
                Cut::CancelInitial,
                Cut::CancelRetry,
                Cut::DeadlineRetryGate,
                Cut::Overwrite,
                Cut::Delete,
            ] {
                let key = format!("native-cuts-{seed}-{direct}-{pooled}-{cut:?}");
                // A bounded key set is rewritten to version A for each repeat.
                client
                    .put_stream_chunks(
                        "recovery-campaign",
                        &key,
                        vec![Bytes::from(payload.clone())],
                    )
                    .await
                    .unwrap();
                let initial = client
                    .lookup_object("recovery-campaign", &key)
                    .await
                    .unwrap()
                    .unwrap();
                let advertised = &initial.placement.as_ref().unwrap().chunks[0].rdma_endpoint;
                let relays = [
                    Relay::start(0, setting("CS_RAIL_LISTENER0", "10.81.1.1:25453")),
                    Relay::start(1, setting("CS_RAIL_LISTENER1", "10.82.1.1:25454")),
                ];
                let mut configured = make_reader(advertised, &relays, direct, pooled);
                if cut == Cut::AckTimeout {
                    configured.limits.io_timeout = Duration::from_millis(300);
                }
                if cut == Cut::DeadlineRetryGate {
                    configured.recovery.as_mut().unwrap().timeout = Duration::from_millis(300);
                }
                let reader = Arc::new(configured);
                for repeat in 0..repeats {
                    if repeat != 0 {
                        client
                            .put_stream_chunks(
                                "recovery-campaign",
                                &key,
                                vec![Bytes::from(payload.clone())],
                            )
                            .await
                            .unwrap();
                    }
                    let before_lookup = client
                        .lookup_object("recovery-campaign", &key)
                        .await
                        .unwrap()
                        .unwrap();
                    let before = reader.recovery_snapshot();
                    for id in ["rail0", "rail1"] {
                        assert!(reader.set_enabled(id, true));
                    }
                    let cancel = RailCancel::default();
                    let op = Arc::new(Operation {
                        id: op_id,
                        control: Control::F2,
                        failed_lane: ((repeat as u64 + seed) % 2) as usize,
                        fault: cut != Cut::CancelInitial,
                        cut,
                        cancel: Some(cancel.clone()),
                        state: Mutex::new(OperationState::default()),
                        changed: Condvar::new(),
                    });
                    for relay in &relays {
                        relay.arm(&op);
                    }
                    let mut reading = client.clone();
                    let read_reader = Arc::clone(&reader);
                    let read_key = key.clone();
                    let size = payload.len();
                    let read_task = tokio::spawn(async move {
                        let mut destination = vec![0xA5; size];
                        let result = reading
                            .read_multi_rail_into(
                                read_reader,
                                "recovery-campaign",
                                &read_key,
                                &mut destination,
                                Some(cancel),
                            )
                            .await;
                        (result, destination)
                    });
                    if matches!(cut, Cut::Overwrite | Cut::Delete) {
                        let held = Arc::clone(&op);
                        tokio::task::spawn_blocking(move || {
                            held.wait(|state| state.held_retry_ack)
                        })
                        .await
                        .unwrap()
                        .unwrap();
                        if cut == Cut::Overwrite {
                            client
                                .put_stream_chunks(
                                    "recovery-campaign",
                                    &key,
                                    vec![Bytes::from(version_b.clone())],
                                )
                                .await
                                .unwrap();
                            let changed = client
                                .lookup_object("recovery-campaign", &key)
                                .await
                                .unwrap()
                                .unwrap();
                            assert_ne!(
                                changed.descriptor.content_etag,
                                before_lookup.descriptor.content_etag
                            );
                            eprintln!("RECOVERY_MUTATION op={op_id} cut={cut:?} key={key} old_generation={} new_generation={} version_b_hash={:016x} observed_before_retry_ack_release=true", before_lookup.descriptor.object_generation, changed.descriptor.object_generation, twox_hash::xxh3::hash64(&version_b));
                        } else {
                            assert!(client.delete("recovery-campaign", &key).await.unwrap());
                            assert!(client
                                .lookup_object("recovery-campaign", &key)
                                .await
                                .unwrap()
                                .is_none());
                            eprintln!("RECOVERY_MUTATION op={op_id} cut={cut:?} key={key} old_generation={} observed_deleted_before_retry_ack_release=true", before_lookup.descriptor.object_generation);
                        }
                        let mut state = op.state.lock().unwrap();
                        state.release_retry_ack = true;
                        op.changed.notify_all();
                    }
                    let (result, destination) = read_task.await.unwrap();
                    let terminal = if cut == Cut::AckTimeout {
                        assert_eq!(result.unwrap(), Some(payload.len()));
                        assert_eq!(destination, payload);
                        "exact_publication_after_ack_timeout"
                    } else {
                        let error = result.unwrap_err();
                        let expected = match cut {
                            Cut::CancelInitial | Cut::CancelRetry => matches!(
                                error.downcast_ref::<RailReadError>(),
                                Some(RailReadError::Cancelled)
                            ),
                            Cut::DeadlineRetryGate => matches!(
                                error.downcast_ref::<RailReadError>(),
                                Some(RailReadError::DeadlineExceeded)
                            ),
                            Cut::Overwrite | Cut::Delete => matches!(
                                error.downcast_ref::<RailReadError>(),
                                Some(RailReadError::VersionChanged)
                            ),
                            _ => unreachable!(),
                        };
                        assert!(expected, "unexpected rejection for {cut:?}: {error:?}");
                        assert!(destination.iter().all(|byte| *byte == 0xA5));
                        "expected_atomic_rejection"
                    };
                    if cut == Cut::DeadlineRetryGate {
                        let mut state = op.state.lock().unwrap();
                        state.retry_registered = true;
                        op.changed.notify_all();
                    }
                    if op.fault {
                        op.wait(|state| state.old_terminal).unwrap();
                    }
                    assert_quiescent(&reader);
                    let after = reader.recovery_snapshot();
                    let retries = after.retry_tasks - before.retry_tasks;
                    assert_eq!(
                        retries,
                        if matches!(cut, Cut::CancelInitial | Cut::DeadlineRetryGate) {
                            0
                        } else {
                            1
                        }
                    );
                    let state = op.state.lock().unwrap();
                    for line in &state.frames {
                        eprintln!("{line}");
                    }
                    eprintln!("RECOVERY_CUT op={op_id} seed={seed} cut={cut:?} direct={direct} pooled={pooled} failed_lane={} repeat={repeat} terminal={terminal} retry_tasks={retries} retry_bytes={} initial_hash={:016x} old_terminal={} budget_quiescent=true", op.failed_lane, after.requested_payload_bytes - before.requested_payload_bytes, twox_hash::xxh3::hash64(&payload), state.old_terminal);
                    op_id += 1;
                }
                drop(reader);
                for relay in relays {
                    relay.finish();
                }
            }
        }
    }
    assert_eq!(op_id, 24 * repeats);
    eprintln!("RECOVERY_CUT_CAMPAIGN_COMPLETE operations={op_id} repeats_per_cut_mode={repeats} incorrect_publications=0 unexpected_errors=0 resource_overshoots=0 deadline_final_publish=native_not_claimed capability_change=native_not_claimed");
}

#[tokio::test]
#[ignore = "requires isolated RXE; healthy pooled reuse and Reader close/recreate campaign"]
async fn native_recovery_reader_lifetime_campaign() {
    let count: usize = setting("CS_LIFETIME_OPS", "1000").parse().unwrap();
    assert_eq!(count % 2, 0);
    let seed: u64 = setting("CS_RECOVERY_SEED", "2026102905").parse().unwrap();
    let mut client = KvClient::connect(setting("CS_RAIL_COORDINATOR", "http://10.81.1.1:25451"))
        .await
        .unwrap();
    let payload: Vec<u8> = (0..1024 * 1024)
        .map(|offset| ((offset as u64 * 19 + seed) % 251) as u8)
        .collect();
    let key = format!("native-lifetime-{seed}");
    client
        .put_stream_chunks(
            "recovery-campaign",
            &key,
            vec![Bytes::from(payload.clone())],
        )
        .await
        .unwrap();
    let initial = client
        .lookup_object("recovery-campaign", &key)
        .await
        .unwrap()
        .unwrap();
    let advertised = &initial.placement.as_ref().unwrap().chunks[0].rdma_endpoint;
    let mut destination = vec![0xA5; payload.len()];
    let mut op_id = 0;
    for close_each in [false, true] {
        for direct in [false, true] {
            let relays = [
                Relay::start(0, setting("CS_RAIL_LISTENER0", "10.81.1.1:25453")),
                Relay::start(1, setting("CS_RAIL_LISTENER1", "10.82.1.1:25454")),
            ];
            let mut persistent = Some(Arc::new(make_reader(advertised, &relays, direct, true)));
            for index in 0..count / 2 {
                let reader = persistent
                    .take()
                    .unwrap_or_else(|| Arc::new(make_reader(advertised, &relays, direct, true)));
                let op = Arc::new(Operation {
                    id: op_id,
                    control: Control::F2,
                    failed_lane: 0,
                    fault: false,
                    cut: Cut::LateEof,
                    cancel: None,
                    state: Mutex::new(OperationState::default()),
                    changed: Condvar::new(),
                });
                for relay in &relays {
                    relay.arm(&op);
                }
                destination.fill(0xA5);
                assert_eq!(
                    client
                        .read_multi_rail_into(
                            Arc::clone(&reader),
                            "recovery-campaign",
                            &key,
                            &mut destination,
                            None
                        )
                        .await
                        .unwrap(),
                    Some(payload.len())
                );
                assert_eq!(destination, payload);
                assert_quiescent(&reader);
                op.wait(|state| state.frames.len() == 2).unwrap();
                let pool = reader.connection_pool_snapshot().unwrap();
                assert_eq!(pool.created, 2);
                assert_eq!(pool.reused, if close_each { 0 } else { (index * 2) as u64 });
                assert_eq!(pool.live, 2);
                assert_eq!(pool.idle, 2);
                assert_eq!(pool.retired, 0);
                for line in &op.state.lock().unwrap().frames {
                    eprintln!("{line}");
                }
                eprintln!("RECOVERY_LIFETIME op={op_id} seed={seed} close_each={close_each} direct={direct} pooled=true key={key} expected_hash={:016x} exact_payload=true pool={pool:?} budget_quiescent=true", twox_hash::xxh3::hash64(&payload));
                if close_each {
                    drop(reader);
                } else {
                    persistent = Some(reader);
                }
                op_id += 1;
            }
            drop(persistent);
            for relay in relays {
                relay.finish();
            }
        }
    }
    assert_eq!(op_id, 2 * count);
    eprintln!("RECOVERY_LIFETIME_COMPLETE healthy_reuse_operations={count} reader_close_recreate_operations={count} total={op_id} incorrect_publications=0 unexpected_errors=0 resource_overshoots=0");
}
