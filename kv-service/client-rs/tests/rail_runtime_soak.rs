//! Bounded, opt-in single-HCA stability campaign through the public read API.
#![cfg(feature = "rdma")]

use contextstore_client_rs::rail_read::{
    RailConnectionPoolOptions, RailConnectionPoolSnapshot, RailLimits, RailReader, RailRoute,
};
use contextstore_client_rs::rdma::RdmaClientConfig;
use contextstore_client_rs::{KvClient, ObjectLookup};
use prost::bytes::Bytes;
use std::hash::Hasher;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use twox_hash::XxHash64;

const SIZE: usize = 8 * 1024 * 1024;
const CONCURRENCY: usize = 4;
const NAMESPACE: &str = "rail-wave2-soak";

fn json(value: &str) -> String {
    let mut result = String::from("\"");
    for character in value.chars() {
        match character {
            '"' => result.push_str("\\\""),
            '\\' => result.push_str("\\\\"),
            '\n' => result.push_str("\\n"),
            '\r' => result.push_str("\\r"),
            '\t' => result.push_str("\\t"),
            c if c.is_control() => result.push_str(&format!("\\u{:04x}", c as u32)),
            c => result.push(c),
        }
    }
    result.push('"');
    result
}

fn hash(bytes: &[u8]) -> u64 {
    let mut hash = XxHash64::with_seed(0);
    hash.write(bytes);
    hash.finish()
}

fn exact_payload(actual: &[u8], expected: &[u8], expected_hash: u64) -> bool {
    actual == expected && hash(actual) == expected_hash
}

#[test]
fn oracle_rejects_corrupt_and_truncated_payloads() {
    let expected = [1, 4, 9, 16, 25];
    assert!(exact_payload(&expected, &expected, hash(&expected)));
    assert!(!exact_payload(
        &[1, 4, 8, 16, 25],
        &expected,
        hash(&expected)
    ));
    assert!(!exact_payload(&expected[..4], &expected, hash(&expected)));
    assert!(!exact_payload(&expected, &expected, hash(&expected) ^ 1));
}

#[test]
#[ignore = "intentional negative control; must exit nonzero"]
fn negative_control_corrupt_payload_must_fail() {
    assert!(exact_payload(
        &[1, 4, 8, 16, 25],
        &[1, 4, 9, 16, 25],
        hash(&[1, 4, 9, 16, 25])
    ));
}

#[derive(Clone, Copy)]
struct Process {
    fd: usize,
    rss_kib: u64,
    threads: u64,
}

fn process() -> Process {
    let status = std::fs::read_to_string("/proc/self/status").expect("read process status");
    let field = |name| {
        status
            .lines()
            .find_map(|line| line.strip_prefix(name))
            .expect("process field")
            .split_whitespace()
            .next()
            .unwrap()
            .parse()
            .unwrap()
    };
    Process {
        fd: std::fs::read_dir("/proc/self/fd")
            .expect("process descriptors")
            .count(),
        rss_kib: field("VmRSS:"),
        threads: field("Threads:"),
    }
}

// Counts are usable only after a nonzero PID-owned resource was observed.
// A successful empty provider report by itself does not establish availability.
fn native(phase: &str) -> [Option<usize>; 3] {
    let pid = std::process::id().to_string();
    std::array::from_fn(|index| {
        let kind = ["qp", "cq", "mr"][index];
        match std::process::Command::new("rdma")
            .args(["resource", "show", kind])
            .output()
        {
            Ok(output) if output.status.success() => {
                let raw = String::from_utf8_lossy(&output.stdout);
                let count = raw
                    .lines()
                    .filter(|line| {
                        line.split_whitespace()
                            .collect::<Vec<_>>()
                            .windows(2)
                            .any(|pair| pair[0] == "pid" && pair[1] == pid)
                    })
                    .count();
                println!("{{\"event\":\"native\",\"phase\":{},\"kind\":{},\"pid\":{},\"pid_matches\":{},\"availability\":\"requires_positive_control\",\"raw\":{}}}", json(phase), json(kind), pid, count, json(&raw));
                Some(count)
            }
            outcome => {
                println!("{{\"event\":\"native\",\"phase\":{},\"kind\":{},\"pid\":{},\"pid_matches\":null,\"availability\":\"unavailable\",\"error\":{}}}", json(phase), json(kind), pid, json(&format!("{outcome:?}")));
                None
            }
        }
    })
}

fn check_quiescent(reader: &RailReader, previous: &mut RailConnectionPoolSnapshot, completed: u64) {
    let pool = reader.connection_pool_snapshot().expect("pool enabled");
    assert!(
        pool.live <= CONCURRENCY,
        "live pool exceeds configured limit"
    );
    assert_eq!(
        pool.live,
        pool.connecting + pool.active + pool.idle + pool.retiring
    );
    assert_eq!((pool.connecting, pool.active, pool.retiring), (0, 0, 0));
    assert_eq!(pool.rejected, 0, "unexpected capacity rejection");
    assert_eq!(
        pool.created.checked_sub(pool.retired),
        Some(pool.live as u64)
    );
    assert_eq!(pool.created.checked_add(pool.reused), Some(completed));
    assert!(
        pool.created >= previous.created
            && pool.reused >= previous.reused
            && pool.retired >= previous.retired
    );
    for rail in reader.snapshots() {
        assert_eq!(
            (
                rail.inflight_requests,
                rail.inflight_bytes,
                rail.registered_bytes
            ),
            (0, 0, 0)
        );
        assert_eq!(rail.reads_err, 0, "unexpected rail error");
        assert_eq!(
            rail.reads_ok, completed,
            "acknowledged requests must reconcile"
        );
        assert_eq!(
            rail.bytes,
            completed.checked_mul(SIZE as u64).unwrap(),
            "acknowledged bytes must reconcile"
        );
    }
    assert_eq!(reader.recovery_snapshot().retry_tasks, 0);
    *previous = pool;
}

fn snapshot(phase: &str, elapsed: f64, reader: &RailReader, completed: u64) -> Process {
    let resource = process();
    let pool = reader.connection_pool_snapshot().unwrap();
    let rail = reader.snapshots().remove(0);
    println!("{{\"event\":\"reconciled\",\"phase\":{},\"completed_requests\":{completed},\"completed_bytes\":{},\"created_plus_reused\":{},\"created_minus_retired\":{},\"pool_live\":{},\"rail_reads_ok\":{},\"rail_bytes\":{}}}", json(phase), completed * SIZE as u64, pool.created + pool.reused, pool.created - pool.retired, pool.live, rail.reads_ok, rail.bytes);
    println!("{{\"event\":\"resources\",\"phase\":{},\"elapsed_s\":{elapsed:.6},\"pid\":{},\"fd\":{},\"rss_kib\":{},\"threads\":{},\"pool_live\":{},\"pool_idle\":{},\"pool_active\":{},\"pool_connecting\":{},\"pool_retiring\":{},\"created\":{},\"reused\":{},\"retired\":{},\"rejected\":{},\"registered_bytes_reserved\":{},\"inflight_bytes\":{},\"inflight_requests\":{},\"rail_reads_ok\":{},\"rail_reads_err\":{},\"rail_bytes\":{}}}", json(phase), std::process::id(), resource.fd, resource.rss_kib, resource.threads, pool.live, pool.idle, pool.active, pool.connecting, pool.retiring, pool.created, pool.reused, pool.retired, pool.rejected, rail.registered_bytes, rail.inflight_bytes, rail.inflight_requests, rail.reads_ok, rail.reads_err, rail.bytes);
    resource
}

struct Object {
    key: String,
    payload: Vec<u8>,
    hash: u64,
    lookup: ObjectLookup,
}

#[derive(Default)]
struct ActiveSamples {
    samples: u64,
    active_samples: u64,
    max_active: usize,
    max_live: usize,
}

impl ActiveSamples {
    fn observe(&mut self, pool: RailConnectionPoolSnapshot) {
        self.samples += 1;
        self.active_samples += u64::from(pool.active > 0);
        self.max_active = self.max_active.max(pool.active);
        self.max_live = self.max_live.max(pool.live);
    }

    fn emit(&self, phase: &str) {
        println!("{{\"event\":\"active_samples\",\"phase\":{},\"sample_count\":{},\"active_sample_count\":{},\"max_active\":{},\"max_live\":{}}}", json(phase), self.samples, self.active_samples, self.max_active, self.max_live);
    }
}

async fn burst(
    client: &KvClient,
    reader: &Arc<RailReader>,
    objects: &[Object],
    sequence: &mut u64,
    phase: &str,
    samples: &mut ActiveSamples,
) {
    let barrier = Arc::new(tokio::sync::Barrier::new(CONCURRENCY));
    let mut reads = Vec::new();
    for _ in 0..CONCURRENCY {
        let seq = *sequence;
        *sequence += 1;
        let object = &objects[seq as usize % objects.len()];
        let mut client = client.clone();
        let reader = Arc::clone(reader);
        let barrier = Arc::clone(&barrier);
        reads.push(async move {
            let mut destination = vec![0xA5; SIZE];
            barrier.wait().await;
            let start = Instant::now();
            let result = client.read_multi_rail_into(reader, NAMESPACE, &object.key, &mut destination, None).await;
            let latency_us = start.elapsed().as_micros();
            let actual_hash = hash(&destination);
            let identity = client.lookup_object(NAMESPACE, &object.key).await;
            let identity_ok = matches!(&identity, Ok(Some(current)) if current.descriptor == object.lookup.descriptor && current.placement == object.lookup.placement);
            let exact = exact_payload(&destination, &object.payload, object.hash);
            let okay = matches!(&result, Ok(Some(SIZE))) && exact && identity_ok;
            println!("{{\"event\":\"request\",\"phase\":{},\"seq\":{seq},\"key\":{},\"bytes\":{},\"hash_xxh64\":\"{actual_hash:016x}\",\"expected_hash_xxh64\":\"{:016x}\",\"latency_us\":{latency_us},\"exact_bytes\":{exact},\"same_identity\":{identity_ok},\"outcome\":{},\"read_result\":{},\"identity_error\":{}}}", json(phase), json(&object.key), match &result { Ok(Some(size)) => *size, _ => 0 }, object.hash, json(if okay { "ok" } else { "error" }), json(&format!("{result:?}")), json(&identity.err().map(|error| error.to_string()).unwrap_or_default()));
            okay
        });
    }
    let mut pending = Box::pin(futures::future::join_all(reads));
    let results = loop {
        tokio::select! {
            results = &mut pending => break results,
            _ = tokio::time::sleep(Duration::from_millis(1)) => {
                let pool = reader.connection_pool_snapshot().unwrap();
                assert!(pool.live <= CONCURRENCY, "active pool exceeds configured limit");
                assert_eq!(pool.live, pool.connecting + pool.active + pool.idle + pool.retiring);
                samples.observe(pool);
            }
        }
    };
    assert!(
        results.iter().all(|okay| *okay),
        "read/content/identity failure; every burst result was logged"
    );
}

#[tokio::test(flavor = "multi_thread", worker_threads = 4)]
#[ignore = "requires isolated physical HCA fixture; defaults to 7200 seconds"]
async fn healthy_single_hca_two_hour_runtime() {
    let duration = std::env::var("CS_SOAK_SECONDS")
        .unwrap_or_else(|_| "7200".into())
        .parse::<u64>()
        .unwrap();
    assert!(
        duration == 7200 || (30..=60).contains(&duration),
        "only 30-60s smoke or full 7200s campaign permitted"
    );
    let epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap()
        .as_nanos();
    let coordinator = std::env::var("CS_RAIL_COORDINATOR").expect("coordinator");
    let listener = std::env::var("CS_RAIL_LISTENER0").expect("listener");
    let device = std::env::var("CS_RAIL_DEVICE0").expect("device");
    let gid = std::env::var("CS_RAIL_GID0").expect("gid").parse().unwrap();
    let mut client = KvClient::connect(coordinator)
        .await
        .expect("connect coordinator");
    let mut objects = Vec::new();
    for seed in 0..3u8 {
        let key = format!("soak-{epoch}-{seed}");
        let payload: Vec<u8> = (0..SIZE)
            .map(|index| ((index % 251) as u8) ^ (seed * 53))
            .collect();
        let payload_hash = hash(&payload);
        let bytes = Bytes::from(payload.clone());
        assert!(client
            .put_stream_chunks(
                NAMESPACE,
                &key,
                vec![bytes.slice(..SIZE / 2), bytes.slice(SIZE / 2..)]
            )
            .await
            .expect("seed object"));
        let lookup = client
            .lookup_object(NAMESPACE, &key)
            .await
            .expect("initial lookup")
            .expect("seeded object");
        assert!(lookup.descriptor.is_striped && lookup.descriptor.stripe_count >= 2);
        assert_eq!(lookup.descriptor.size as usize, SIZE);
        println!("{{\"event\":\"object\",\"key\":{},\"bytes\":{SIZE},\"hash_xxh64\":\"{payload_hash:016x}\",\"generation\":{},\"descriptor\":{},\"placement\":{}}}", json(&key), lookup.descriptor.object_generation, json(&format!("{:?}", lookup.descriptor)), json(&format!("{:?}", lookup.placement)));
        objects.push(Object {
            key,
            payload,
            hash: payload_hash,
            lookup,
        });
    }
    let advertised = &objects[0].lookup.placement.as_ref().unwrap().chunks[0].rdma_endpoint;
    assert!(objects.iter().all(|object| object
        .lookup
        .placement
        .as_ref()
        .unwrap()
        .chunks
        .iter()
        .all(|chunk| &chunk.rdma_endpoint == advertised)));
    let make_reader = || {
        Arc::new(
            RailReader::new(
                vec![RailRoute::new(
                    "physical-single-hca",
                    advertised,
                    RdmaClientConfig::new(&listener, &device).with_gid_index(gid),
                )],
                RailLimits {
                    max_active_reads: CONCURRENCY,
                    max_active_reads_per_rail: CONCURRENCY,
                    max_staging_bytes: 128 * 1024 * 1024,
                    max_registered_bytes: 64 * 1024 * 1024,
                    max_inflight_bytes: 64 * 1024 * 1024,
                    max_inflight_bytes_per_rail: 64 * 1024 * 1024,
                    io_timeout: Duration::from_secs(10),
                    ..RailLimits::default()
                },
            )
            .unwrap()
            .with_connection_pool(RailConnectionPoolOptions {
                max_live_connections: CONCURRENCY,
                configuration_generation: 1,
            })
            .unwrap(),
        )
    };
    let before = process();
    let native_before = native("before_reader");
    println!("{{\"event\":\"contract\",\"pid\":{},\"duration_s\":{duration},\"object_bytes\":{SIZE},\"objects\":3,\"concurrency\":{CONCURRENCY},\"pool_limit\":4,\"recovery\":false,\"before_fd\":{},\"before_rss_kib\":{},\"before_threads\":{},\"fd_growth_guard\":8,\"thread_growth_guard\":4,\"rss_growth_guard_kib\":131072,\"resource_interval_s\":30,\"continuous_reader\":true,\"hash\":\"xxh64_seed0\"}}", std::process::id(), before.fd, before.rss_kib, before.threads);
    let reader = make_reader();
    let mut sequence = 0;
    let mut previous = RailConnectionPoolSnapshot::default();
    let mut warm_samples = ActiveSamples::default();
    for _ in 0..20 {
        burst(
            &client,
            &reader,
            &objects,
            &mut sequence,
            "warmup",
            &mut warm_samples,
        )
        .await;
        check_quiescent(&reader, &mut previous, sequence);
    }
    assert_eq!(
        previous.live, CONCURRENCY,
        "warmup must exercise maximum pool concurrency"
    );
    warm_samples.emit("warm");
    assert!(
        warm_samples.active_samples > 0,
        "warmup must observe active leases"
    );
    let warm = snapshot("warm", 0.0, &reader, sequence);
    let native_warm = native("warm");
    let started = Instant::now();
    let first_sequence = sequence;
    let mut last_snapshot = Instant::now();
    let mut max_rss = warm.rss_kib;
    let mut max_fd = warm.fd;
    let mut max_threads = warm.threads;
    let mut steady_samples = ActiveSamples::default();
    while started.elapsed() < Duration::from_secs(duration) {
        burst(
            &client,
            &reader,
            &objects,
            &mut sequence,
            "steady",
            &mut steady_samples,
        )
        .await;
        check_quiescent(&reader, &mut previous, sequence);
        if last_snapshot.elapsed() >= Duration::from_secs(30) {
            let current = snapshot("steady", started.elapsed().as_secs_f64(), &reader, sequence);
            steady_samples.emit("steady");
            native("steady");
            max_rss = max_rss.max(current.rss_kib);
            max_fd = max_fd.max(current.fd);
            max_threads = max_threads.max(current.threads);
            assert!(current.fd <= warm.fd + 8, "FD diagnostic guard exceeded");
            assert!(
                current.threads <= warm.threads + 4,
                "thread diagnostic guard exceeded"
            );
            assert!(
                current.rss_kib <= warm.rss_kib + 128 * 1024,
                "RSS diagnostic guard exceeded"
            );
            last_snapshot = Instant::now();
        }
    }
    let elapsed = started.elapsed().as_secs_f64();
    steady_samples.emit("steady_complete");
    assert!(
        steady_samples.active_samples > 0,
        "steady run must observe active leases"
    );
    let final_process = snapshot("steady_complete", elapsed, &reader, sequence);
    assert!(
        final_process.fd <= warm.fd + 8,
        "final FD diagnostic guard exceeded"
    );
    assert!(
        final_process.threads <= warm.threads + 4,
        "final thread diagnostic guard exceeded"
    );
    assert!(
        final_process.rss_kib <= warm.rss_kib + 128 * 1024,
        "final RSS diagnostic guard exceeded"
    );
    native("steady_complete");
    assert_eq!(Arc::strong_count(&reader), 1, "no read still owns reader");
    drop(reader);
    let settle = Instant::now();
    while process().fd != before.fd && settle.elapsed() < Duration::from_secs(5) {
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    let dropped = process();
    let native_after = native("after_reader_drop");
    println!("{{\"event\":\"drop\",\"before_fd\":{},\"after_fd\":{},\"fd_delta_from_before\":{},\"before_rss_kib\":{},\"warm_rss_kib\":{},\"after_rss_kib\":{},\"rss_delta_from_before_kib\":{},\"rss_delta_from_warm_kib\":{},\"before_threads\":{},\"warm_threads\":{},\"after_threads\":{},\"thread_delta_from_before\":{},\"thread_delta_from_warm\":{},\"settle_s\":{:.6},\"rss_guard\":\"warm_plus_131072_KiB_not_exact_baseline_return\",\"thread_guard\":\"warm_plus_4\"}}", before.fd, dropped.fd, dropped.fd as i64 - before.fd as i64, before.rss_kib, warm.rss_kib, dropped.rss_kib, dropped.rss_kib as i64 - before.rss_kib as i64, dropped.rss_kib as i64 - warm.rss_kib as i64, before.threads, warm.threads, dropped.threads, dropped.threads as i64 - before.threads as i64, dropped.threads as i64 - warm.threads as i64, settle.elapsed().as_secs_f64());
    assert_eq!(
        dropped.fd, before.fd,
        "FD count must return to pre-reader baseline"
    );
    assert!(
        dropped.rss_kib <= warm.rss_kib + 128 * 1024,
        "post-Drop RSS diagnostic guard exceeded"
    );
    assert!(
        dropped.threads <= warm.threads + 4,
        "post-Drop thread diagnostic guard exceeded"
    );
    for index in 0..3 {
        let visible = native_warm[index].is_some_and(|count| count > 0);
        println!(
            "{{\"event\":\"native_availability\",\"kind\":{},\"available\":{visible}}}",
            json(["qp", "cq", "mr"][index])
        );
        if visible {
            assert_eq!(
                native_after[index], native_before[index],
                "visible native resources must return to pre-reader baseline"
            );
        }
    }
    let reopened = make_reader();
    let mut reopen_samples = ActiveSamples::default();
    burst(
        &client,
        &reopened,
        &objects,
        &mut sequence,
        "reopened",
        &mut reopen_samples,
    )
    .await;
    check_quiescent(
        &reopened,
        &mut RailConnectionPoolSnapshot::default(),
        CONCURRENCY as u64,
    );
    snapshot("reopened", 0.0, &reopened, CONCURRENCY as u64);
    reopen_samples.emit("reopened");
    drop(reopened);
    println!("{{\"event\":\"summary\",\"status\":\"pass\",\"duration_required_s\":{duration},\"steady_elapsed_s\":{elapsed:.6},\"steady_requests\":{},\"steady_bytes\":{},\"errors\":0,\"rejections\":0,\"deadlines\":0,\"warm_rss_kib\":{},\"max_sampled_rss_kib\":{},\"final_rss_kib\":{},\"rss_endpoint_slope_kib_s\":{:.6},\"max_sampled_fd\":{},\"max_sampled_threads\":{},\"drop_fd_restored\":true,\"reopen_reads\":4}}", sequence - first_sequence - 4, (sequence - first_sequence - 4) * SIZE as u64, warm.rss_kib, max_rss.max(final_process.rss_kib), final_process.rss_kib, (final_process.rss_kib as f64 - warm.rss_kib as f64) / elapsed, max_fd.max(final_process.fd), max_threads.max(final_process.threads));
}
