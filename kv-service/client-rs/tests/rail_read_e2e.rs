//! Hardware-gated checks for one Worker reading one object over two RDMA rails.
//!
//! Configure a striped KVService with two independent listeners, then set
//! `CS_RAIL_COORDINATOR`, `CS_RAIL_LISTENER0/1`, and `CS_RAIL_DEVICE0/1`.

#![cfg(feature = "rdma")]

use contextstore_client_rs::rail_read::{
    LocalRailPath, RailCancel, RailConnectionPoolOptions, RailLimits, RailReader,
    RailRecoveryOptions, RailRoute,
};
use contextstore_client_rs::rdma::{RdmaClient, RdmaClientConfig};
use contextstore_client_rs::KvClient;
use prost::bytes::Bytes;
use std::sync::Arc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

fn setting(name: &str, default: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| default.to_string())
}

fn route(advertised: &str, index: usize, listener: &str) -> RailRoute {
    let device = setting(&format!("CS_RAIL_DEVICE{index}"), &format!("rxe{index}"));
    let gid = setting(&format!("CS_RAIL_GID{index}"), "3")
        .parse::<u8>()
        .expect("GID index");
    RailRoute::new(
        format!("rail{index}"),
        advertised,
        RdmaClientConfig::new(listener, device).with_gid_index(gid),
    )
}

async fn seeded_object() -> (KvClient, String, Vec<u8>, String) {
    let coordinator = setting("CS_RAIL_COORDINATOR", "http://127.0.0.1:50051");
    let mut client = KvClient::connect(coordinator).await.expect("connect gRPC");
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("wall clock")
        .as_nanos();
    let key = format!("rail-e2e-{nanos}");
    let mib = setting("CS_RAIL_OBJECT_MIB", "64")
        .parse::<usize>()
        .expect("object size in MiB");
    let payload: Vec<u8> = (0..mib * 1024 * 1024)
        .map(|offset| (offset % 251) as u8)
        .collect();
    let bytes = Bytes::from(payload.clone());
    let segment_size = 4 * 1024 * 1024;
    let segments = (0..bytes.len())
        .step_by(segment_size)
        .map(|offset| bytes.slice(offset..(offset + segment_size).min(bytes.len())))
        .collect();
    client
        .put_stream_chunks("rail-e2e", &key, segments)
        .await
        .expect("seed object");
    let lookup = client
        .lookup_object("rail-e2e", &key)
        .await
        .expect("lookup")
        .expect("seeded object exists");
    assert!(
        lookup.descriptor.is_striped && lookup.descriptor.stripe_count >= 2,
        "configure striped storage and seed an object above its threshold"
    );
    let advertised = lookup
        .placement
        .as_ref()
        .expect("placement")
        .chunks
        .first()
        .expect("stripe")
        .rdma_endpoint
        .clone();
    assert!(
        lookup
            .placement
            .as_ref()
            .expect("placement")
            .chunks
            .iter()
            .all(|chunk| chunk.rdma_endpoint == advertised),
        "this test requires one storage node with two RDMA listeners"
    );
    (client, key, payload, advertised)
}

#[tokio::test]
#[ignore = "requires one reachable HCA or RXE listener and a striped KVService"]
async fn single_real_rail_restores_verified_object() {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &listener)],
            RailLimits::default(),
        )
        .expect("single rail"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert_eq!(
        client
            .read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut destination,
                None
            )
            .await
            .expect("real rail read"),
        Some(payload.len())
    );
    assert_eq!(destination, payload);
    assert_eq!(reader.snapshots()[0].bytes, payload.len() as u64);
}

#[tokio::test]
#[ignore = "requires a striped KVService and an injected dead RDMA listener"]
async fn dead_single_real_rail_leaves_destination_unchanged() {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let dead = setting("CS_RAIL_DEAD_LISTENER", "127.0.0.1:59999");
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &dead)],
            RailLimits {
                io_timeout: Duration::from_secs(3),
                ..RailLimits::default()
            },
        )
        .expect("dead rail"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert!(client
        .read_multi_rail_into(reader, "rail-e2e", &key, &mut destination, None)
        .await
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

#[tokio::test]
#[ignore = "requires one reachable HCA or RXE listener and a large striped object"]
async fn cancellation_after_real_transfer_starts_cannot_write_reused_buffer() {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &listener)],
            RailLimits::default(),
        )
        .expect("single rail"),
    );
    let cancel = RailCancel::default();
    let task_reader = Arc::clone(&reader);
    let task_cancel = cancel.clone();
    let read_task = tokio::spawn(async move {
        let mut destination = vec![0xA5; payload.len()];
        let result = client
            .read_multi_rail_into(
                task_reader,
                "rail-e2e",
                &key,
                &mut destination,
                Some(task_cancel),
            )
            .await;
        (result, destination)
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    while reader.snapshots()[0].inflight_requests == 0 {
        assert!(
            Instant::now() < deadline,
            "real RDMA transfer never started"
        );
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    cancel.cancel();
    let (result, mut destination) = tokio::time::timeout(Duration::from_secs(40), read_task)
        .await
        .expect("cancelled transfer did not quiesce")
        .expect("read task joined");
    assert!(result
        .expect_err("cancelled transfer must fail")
        .to_string()
        .contains("cancelled"));
    assert!(destination.iter().all(|byte| *byte == 0xA5));
    destination.fill(0x33);
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(destination.iter().all(|byte| *byte == 0x33));
    assert_eq!(reader.snapshots()[0].inflight_requests, 0);
    assert_eq!(reader.snapshots()[0].registered_bytes, 0);
}

async fn cancellation_during_two_rail_transfer_case(direct_staging: bool) {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let listener1 = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let reader = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &listener1),
            ],
            RailLimits {
                direct_staging,
                ..RailLimits::default()
            },
        )
        .expect("dual reader"),
    );
    let cancel = RailCancel::default();
    let task_reader = Arc::clone(&reader);
    let task_cancel = cancel.clone();
    let read_task = tokio::spawn(async move {
        let mut destination = vec![0xA5; payload.len()];
        let result = client
            .read_multi_rail_into(
                task_reader,
                "rail-e2e",
                &key,
                &mut destination,
                Some(task_cancel),
            )
            .await;
        (result, destination)
    });
    let deadline = Instant::now() + Duration::from_secs(10);
    while !reader
        .snapshots()
        .iter()
        .all(|snapshot| snapshot.inflight_requests > 0)
    {
        assert!(
            Instant::now() < deadline,
            "both RDMA rails never entered flight"
        );
        tokio::time::sleep(Duration::from_millis(1)).await;
    }
    cancel.cancel();
    let (result, mut destination) = tokio::time::timeout(Duration::from_secs(40), read_task)
        .await
        .expect("cancelled dual transfer did not quiesce")
        .expect("read task joined");
    assert!(result
        .expect_err("cancelled dual transfer must fail")
        .to_string()
        .contains("cancelled"));
    assert!(destination.iter().all(|byte| *byte == 0xA5));
    destination.fill(0x33);
    tokio::time::sleep(Duration::from_millis(100)).await;
    assert!(destination.iter().all(|byte| *byte == 0x33));
    assert!(reader
        .snapshots()
        .iter()
        .all(|snapshot| snapshot.inflight_requests == 0 && snapshot.registered_bytes == 0));
}

#[tokio::test]
#[ignore = "requires two real rails and a large striped object"]
async fn cancellation_during_two_rail_transfer_preserves_reused_buffer() {
    cancellation_during_two_rail_transfer_case(false).await;
}

#[tokio::test]
#[ignore = "requires two real rails and a large striped object"]
async fn cancellation_during_direct_staging_preserves_reused_buffer() {
    cancellation_during_two_rail_transfer_case(true).await;
}

#[tokio::test]
#[ignore = "requires an existing striped object with one deliberately corrupted physical stripe"]
async fn corrupted_real_stripe_does_not_publish_bytes() {
    let coordinator = setting("CS_RAIL_COORDINATOR", "http://127.0.0.1:50051");
    let namespace = setting("CS_RAIL_EXISTING_NAMESPACE", "rust-bench");
    let key = setting("CS_RAIL_EXISTING_KEY", "railtest0/__combined__");
    let mut client = KvClient::connect(coordinator).await.expect("connect gRPC");
    let lookup = client
        .lookup_object(&namespace, &key)
        .await
        .expect("lookup")
        .expect("object exists");
    let placement = lookup.placement.expect("placement");
    assert!(
        placement
            .chunks
            .iter()
            .all(|chunk| !chunk.checksum.is_empty()),
        "checksum injection requires a newly written, checksummed object"
    );
    let advertised = placement.chunks[0].rdma_endpoint.clone();
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &listener)],
            RailLimits::default(),
        )
        .expect("single rail"),
    );
    let mut destination = vec![0xA5; lookup.descriptor.size as usize];
    assert!(client
        .read_multi_rail_into(reader, &namespace, &key, &mut destination, None)
        .await
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

#[tokio::test]
#[ignore = "requires two real rails and a deliberately corrupted stripe 1 on an isolated object"]
async fn corrupt_stripe_on_second_rail_cannot_publish_partial_object() {
    let coordinator = setting("CS_RAIL_COORDINATOR", "http://127.0.0.1:50051");
    let namespace = setting("CS_RAIL_EXISTING_NAMESPACE", "rust-bench");
    let key = setting("CS_RAIL_EXISTING_KEY", "rail-corrupt0/__combined__");
    let mut client = KvClient::connect(coordinator).await.expect("connect gRPC");
    let lookup = client
        .lookup_object(&namespace, &key)
        .await
        .expect("lookup")
        .expect("object exists");
    let placement = lookup.placement.expect("placement");
    assert!(
        placement
            .chunks
            .iter()
            .all(|chunk| !chunk.checksum.is_empty()),
        "a newly written checksummed object is required"
    );
    let advertised = placement.chunks[0].rdma_endpoint.clone();
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let listener1 = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let reader = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &listener1),
            ],
            RailLimits::default(),
        )
        .expect("dual reader"),
    );
    let mut destination = vec![0xA5; lookup.descriptor.size as usize];
    assert!(client
        .read_multi_rail_into(
            Arc::clone(&reader),
            &namespace,
            &key,
            &mut destination,
            None
        )
        .await
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
    let snapshots = reader.snapshots();
    assert!(
        snapshots[0].bytes > 0,
        "healthy first rail should have finished"
    );
    assert!(
        snapshots[1].reads_err > 0 || snapshots[1].bytes > 0,
        "second rail must have been involved"
    );
}

#[tokio::test]
#[ignore = "requires CS_RDMA_TEST_PRE_WRITE_DELAY_MS=3000 on an isolated real RDMA server"]
async fn late_server_write_after_timeout_cannot_corrupt_reused_buffer() {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &listener)],
            RailLimits {
                io_timeout: Duration::from_secs(1),
                ..RailLimits::default()
            },
        )
        .expect("single rail"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert!(client
        .read_multi_rail_into(reader, "rail-e2e", &key, &mut destination, None)
        .await
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
    destination.fill(0x33);
    tokio::time::sleep(Duration::from_secs(4)).await;
    assert!(destination.iter().all(|byte| *byte == 0x33));
}

async fn late_second_rail_case(direct_staging: bool) {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let listener1 = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let reader = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &listener1),
            ],
            RailLimits {
                io_timeout: Duration::from_secs(1),
                direct_staging,
                ..RailLimits::default()
            },
        )
        .expect("dual reader"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert!(client
        .read_multi_rail_into(
            Arc::clone(&reader),
            "rail-e2e",
            &key,
            &mut destination,
            None,
        )
        .await
        .is_err());
    let snapshots = reader.snapshots();
    assert_eq!(snapshots[0].reads_ok, 1, "first rail must have completed");
    assert_eq!(snapshots[1].reads_err, 1, "second rail must have failed");
    assert!(destination.iter().all(|byte| *byte == 0xA5));
    destination.fill(0x33);
    tokio::time::sleep(Duration::from_secs(8)).await;
    assert!(destination.iter().all(|byte| *byte == 0x33));
    assert!(reader
        .snapshots()
        .iter()
        .all(|snapshot| snapshot.inflight_requests == 0 && snapshot.registered_bytes == 0));
}

#[tokio::test]
#[ignore = "requires two real rails and a three-second pre-WRITE delay on server nic_idx=1"]
async fn late_second_rail_after_first_completion_cannot_publish_or_corrupt() {
    late_second_rail_case(false).await;
}

#[tokio::test]
#[ignore = "requires two real rails and a three-second pre-WRITE delay on server nic_idx=1"]
async fn late_second_rail_direct_staging_cannot_publish_or_corrupt() {
    late_second_rail_case(true).await;
}

#[tokio::test]
#[ignore = "requires an isolated RXE server with CS_FORCE_DISK_READ=1 and a 64 MiB object"]
async fn legacy_get_timeout_retires_qp_before_buffer_reuse() {
    let (_client, key, payload, advertised) = seeded_object().await;
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let device = setting("CS_RAIL_DEVICE0", "rxe_c0");
    let gid = setting("CS_RAIL_GID0", "1")
        .parse::<u8>()
        .expect("GID index");
    let mut rdma = RdmaClient::connect(RdmaClientConfig::new(listener, device).with_gid_index(gid))
        .expect("connect legacy GET");
    rdma.set_io_timeout(Duration::from_millis(1))
        .expect("set GET deadline after handshake");
    let mut destination = vec![0xA5; payload.len()];
    let registered = rdma
        .register_buffer(&mut destination)
        .expect("register buffer");
    assert!(rdma.get_into("rail-e2e", &key, &registered, 0).is_err());
    assert!(
        rdma.get_into("rail-e2e", &key, &registered, 0).is_err(),
        "failed legacy GET must not reuse the old QP"
    );
    drop(rdma);
    drop(registered);
    destination.fill(0x33);
    tokio::time::sleep(Duration::from_secs(6)).await;
    assert!(destination.iter().all(|byte| *byte == 0x33));
}

#[tokio::test]
#[ignore = "requires a three-second delay on server nic_idx=1 and a two-second CQ deadline"]
async fn uncertain_completion_retires_old_server_connection() {
    let (mut client, key, _payload, _advertised) = seeded_object().await;
    let descriptor = client
        .lookup_object("rail-e2e", &key)
        .await
        .expect("lookup")
        .expect("seeded object")
        .descriptor;
    let device = setting("CS_RAIL_DEVICE1", "rxe_c1");
    let listener = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let gid = setting("CS_RAIL_GID1", "1")
        .parse::<u8>()
        .expect("GID index");
    let mut rdma = RdmaClient::connect(
        RdmaClientConfig::new(listener, device)
            .with_gid_index(gid)
            .with_io_timeout(Duration::from_secs(1)),
    )
    .expect("second rail connects");
    let mut target = vec![0u8; descriptor.size as usize];
    let registered = rdma.register_buffer(&mut target).expect("register buffer");
    let view = registered.view();
    let segments = [(view.addr(), view.rkey(), descriptor.size)];
    assert!(rdma
        .get_descriptor_stripes_sge_detailed(&descriptor, &[1], &segments)
        .is_err());
    tokio::time::sleep(Duration::from_secs(6)).await;
    assert!(
        rdma.get_descriptor_stripes_sge_detailed(&descriptor, &[1], &segments)
            .is_err(),
        "server must close the old QP/CQ rather than return a stale response"
    );
    drop(rdma);
    drop(registered);
}

#[tokio::test]
#[ignore = "requires two reachable RDMA listeners on the same storage node"]
async fn same_object_matches_single_and_dual_rail() {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let listener1 = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let limits = RailLimits {
        io_timeout: Duration::from_secs(10),
        ..RailLimits::default()
    };
    let dual = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &listener1),
            ],
            limits.clone(),
        )
        .expect("dual reader"),
    );
    let mut dual_bytes = vec![0xA5; payload.len()];
    assert_eq!(
        client
            .read_multi_rail_into(Arc::clone(&dual), "rail-e2e", &key, &mut dual_bytes, None)
            .await
            .expect("dual read"),
        Some(payload.len())
    );
    assert_eq!(dual_bytes, payload);
    assert!(dual.snapshots().iter().all(|snapshot| snapshot.bytes > 0));

    let single = Arc::new(
        RailReader::new(vec![route(&advertised, 0, &listener0)], limits).expect("single reader"),
    );
    let mut single_bytes = vec![0x5A; payload.len()];
    assert_eq!(
        client
            .read_multi_rail_into(single, "rail-e2e", &key, &mut single_bytes, None)
            .await
            .expect("single read"),
        Some(payload.len())
    );
    assert_eq!(single_bytes, dual_bytes);
}

fn discovered_local_paths() -> [LocalRailPath; 2] {
    [
        LocalRailPath::new(
            "rail0",
            setting("CS_RAIL_FABRIC0", "fabric-a"),
            setting("CS_RAIL_DEVICE0", "rxe_c0"),
        )
        .with_gid_index(
            setting("CS_RAIL_GID0", "1")
                .parse::<u8>()
                .expect("first rail GID"),
        ),
        LocalRailPath::new(
            "rail1",
            setting("CS_RAIL_FABRIC1", "fabric-b"),
            setting("CS_RAIL_DEVICE1", "rxe_c1"),
        )
        .with_gid_index(
            setting("CS_RAIL_GID1", "1")
                .parse::<u8>()
                .expect("second rail GID"),
        ),
    ]
}

#[tokio::test]
#[ignore = "requires two real rails and server-advertised fabric/listener capabilities"]
async fn advertised_two_rails_restore_one_object() {
    let (mut client, key, payload, _advertised) = seeded_object().await;
    let lookup = client
        .lookup_object("rail-e2e", &key)
        .await
        .expect("lookup")
        .expect("seeded object");
    let reader = Arc::new(
        RailReader::discover_from_placement(
            lookup.placement.as_ref().expect("placement"),
            &discovered_local_paths(),
            RailLimits::default(),
        )
        .expect("discover rail listeners"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert_eq!(
        client
            .read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut destination,
                None
            )
            .await
            .expect("discovered dual read"),
        Some(payload.len())
    );
    assert_eq!(destination, payload);
    assert_eq!(reader.snapshots().len(), 2);
    assert!(reader.snapshots().iter().all(|rail| rail.bytes > 0));
}

#[tokio::test]
#[ignore = "requires advertised rails; externally disable the second client RXE link"]
async fn advertised_second_link_down_cannot_publish_partial_object() {
    let (mut client, key, payload, _advertised) = seeded_object().await;
    let lookup = client
        .lookup_object("rail-e2e", &key)
        .await
        .expect("lookup")
        .expect("seeded object");
    let reader = Arc::new(
        RailReader::discover_from_placement(
            lookup.placement.as_ref().expect("placement"),
            &discovered_local_paths(),
            RailLimits {
                io_timeout: Duration::from_secs(4),
                ..RailLimits::default()
            },
        )
        .expect("discover rail listeners"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert!(client
        .read_multi_rail_into(
            Arc::clone(&reader),
            "rail-e2e",
            &key,
            &mut destination,
            None
        )
        .await
        .is_err());
    let rails = reader.snapshots();
    assert_eq!(rails[0].reads_ok, 1, "first rail should have completed");
    assert_eq!(rails[1].reads_err, 1, "disabled second rail should fail");
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

async fn failed_second_rail_case(direct_staging: bool) {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let dead = setting("CS_RAIL_DEAD_LISTENER", "127.0.0.1:59999");
    let limits = RailLimits {
        io_timeout: Duration::from_secs(3),
        direct_staging,
        ..RailLimits::default()
    };
    let reader = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &dead),
            ],
            limits,
        )
        .expect("reader"),
    );
    let mut destination = vec![0xA5; payload.len()];
    assert!(client
        .read_multi_rail_into(reader, "rail-e2e", &key, &mut destination, None)
        .await
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

#[tokio::test]
#[ignore = "requires one reachable RDMA listener and one injected dead listener"]
async fn failed_second_rail_does_not_publish_partial_bytes() {
    failed_second_rail_case(false).await;
}

#[tokio::test]
#[ignore = "requires one reachable RDMA listener and one injected dead listener"]
async fn failed_second_rail_direct_staging_does_not_publish_partial_bytes() {
    failed_second_rail_case(true).await;
}

#[tokio::test]
#[ignore = "requires two reachable RDMA listeners and a striped KVService"]
async fn old_generation_is_rejected_without_publishing_bytes() {
    let (mut client, key, mut payload, advertised) = seeded_object().await;
    let old = client
        .lookup_object("rail-e2e", &key)
        .await
        .expect("lookup")
        .expect("seeded object");
    assert!(client.delete("rail-e2e", &key).await.expect("delete"));
    payload[0] ^= 0xFF;
    let bytes = Bytes::from(payload.clone());
    let segment_size = 4 * 1024 * 1024;
    let segments = (0..bytes.len())
        .step_by(segment_size)
        .map(|offset| bytes.slice(offset..(offset + segment_size).min(bytes.len())))
        .collect();
    client
        .put_stream_chunks("rail-e2e", &key, segments)
        .await
        .expect("rewrite");
    let reader = RailReader::new(
        vec![
            route(&advertised, 0, &setting("CS_RAIL_LISTENER0", &advertised)),
            route(
                &advertised,
                1,
                &setting("CS_RAIL_LISTENER1", "127.0.0.1:50054"),
            ),
        ],
        RailLimits::default(),
    )
    .expect("reader");
    let mut destination = vec![0xA5; payload.len()];
    assert!(reader
        .read_into(
            &old.descriptor,
            old.placement.as_ref().expect("old placement"),
            &mut destination,
            None,
        )
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

/// Empirical race campaign: successful reads must equal one complete written
/// version; every rejected read must leave the caller's sentinel intact.
/// The writer's generations and each outcome are printed for raw audit.
#[tokio::test]
#[ignore = "requires two RXE/HCA Rails, a striped KVService, and repeated writes"]
async fn concurrent_rewrites_never_publish_mixed_direct_staging_bytes() {
    fn segments(bytes: &Bytes) -> Vec<Bytes> {
        const CHUNK: usize = 4 * 1024 * 1024;
        (0..bytes.len())
            .step_by(CHUNK)
            .map(|offset| bytes.slice(offset..(offset + CHUNK).min(bytes.len())))
            .collect()
    }

    let coordinator = setting("CS_RAIL_COORDINATOR", "http://127.0.0.1:50051");
    let mib = setting("CS_RAIL_OBJECT_MIB", "64")
        .parse::<usize>()
        .expect("object size in MiB");
    let size = mib * 1024 * 1024;
    let a = Bytes::from(vec![0x11; size]);
    let b = Bytes::from(vec![0xE7; size]);
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("wall clock")
        .as_nanos();
    let key = format!("direct-race-{nanos}");
    let mut seed = KvClient::connect(coordinator.clone())
        .await
        .expect("connect initial writer");
    assert!(seed
        .put_stream_chunks("rail-e2e", &key, segments(&a))
        .await
        .expect("seed version A"));
    let initial = seed
        .lookup_object("rail-e2e", &key)
        .await
        .expect("initial lookup")
        .expect("seeded object");
    assert!(initial.descriptor.is_striped);
    assert_eq!(initial.descriptor.size as usize, size);
    println!(
        "RACE_CONFIG,object_key={key},object_mib={mib},stripe_count={},direct_staging=true",
        initial.descriptor.stripe_count
    );
    let placement = initial.placement.as_ref().expect("initial placement");
    let advertised = placement
        .chunks
        .first()
        .expect("stripe")
        .rdma_endpoint
        .clone();
    assert!(placement
        .chunks
        .iter()
        .all(|chunk| chunk.rdma_endpoint == advertised));
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let listener1 = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let reader = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &listener1),
            ],
            RailLimits {
                direct_staging: true,
                io_timeout: Duration::from_secs(10),
                ..RailLimits::default()
            },
        )
        .expect("direct reader"),
    );
    let mut client = KvClient::connect(coordinator.clone())
        .await
        .expect("connect reader");
    let writer_key = key.clone();
    let writer = tokio::spawn(async move {
        let mut writer_client = KvClient::connect(coordinator)
            .await
            .expect("connect concurrent writer");
        let mut generations = vec![initial.descriptor.object_generation];
        for revision in 0..15usize {
            let value = if revision % 2 == 0 { &b } else { &a };
            assert!(writer_client
                .put_stream_chunks("rail-e2e", &writer_key, segments(value))
                .await
                .expect("concurrent rewrite"));
            let current = writer_client
                .lookup_object("rail-e2e", &writer_key)
                .await
                .expect("writer lookup")
                .expect("written object");
            let generation = current.descriptor.object_generation;
            println!("RACE_WRITE,revision={revision},generation={generation}");
            generations.push(generation);
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
        generations
    });

    let mut accepted_a = 0usize;
    let mut accepted_b = 0usize;
    let mut rejected = 0usize;
    let mut missing = 0usize;
    for attempt in 0..36usize {
        let mut destination = vec![0xA5; size];
        let result = tokio::time::timeout(
            Duration::from_secs(30),
            client.read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut destination,
                None,
            ),
        )
        .await
        .expect("bounded direct read");
        match result {
            Ok(Some(length)) => {
                assert_eq!(length, size);
                let version = if destination.iter().all(|byte| *byte == 0x11) {
                    accepted_a += 1;
                    "A"
                } else if destination.iter().all(|byte| *byte == 0xE7) {
                    accepted_b += 1;
                    "B"
                } else {
                    panic!("direct read published a mixed or corrupt object at attempt {attempt}");
                };
                println!("RACE_READ,attempt={attempt},result=accepted_{version}");
            }
            Ok(None) => {
                assert!(destination.iter().all(|byte| *byte == 0xA5));
                missing += 1;
                println!("RACE_READ,attempt={attempt},result=missing");
            }
            Err(error) => {
                assert!(destination.iter().all(|byte| *byte == 0xA5));
                rejected += 1;
                println!("RACE_READ,attempt={attempt},result=rejected,error={error}");
            }
        }
        if writer.is_finished() && attempt >= 19 {
            break;
        }
    }
    let generations = writer.await.expect("writer task joined");
    assert!(generations.windows(2).all(|pair| pair[1] > pair[0]));
    assert!(
        accepted_a + accepted_b > 0,
        "no complete version was accepted"
    );

    let mut final_destination = vec![0xA5; size];
    assert_eq!(
        client
            .read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut final_destination,
                None,
            )
            .await
            .expect("post-rewrite direct read"),
        Some(size)
    );
    assert!(final_destination.iter().all(|byte| *byte == 0xE7));
    let modes = reader.mode_counts();
    assert!(modes.direct > 0, "test did not enter direct mode");
    assert_eq!(modes.one + modes.packed_greedy + modes.packed_contiguous, 0);
    println!(
        "RACE_MODE,direct={},one={},packed_greedy={},packed_contiguous={}",
        modes.direct, modes.one, modes.packed_greedy, modes.packed_contiguous
    );
    for rail in reader.snapshots() {
        assert_eq!(rail.inflight_requests, 0);
        assert_eq!(rail.inflight_bytes, 0);
        assert_eq!(rail.registered_bytes, 0);
        assert!(rail.bytes > 0, "both Rail paths must carry payload");
        println!(
            "RACE_RAIL,id={},bytes={},reads_ok={},reads_err={},inflight_requests={},inflight_bytes={},registered_bytes={}",
            rail.id,
            rail.bytes,
            rail.reads_ok,
            rail.reads_err,
            rail.inflight_requests,
            rail.inflight_bytes,
            rail.registered_bytes,
        );
    }
    println!(
        "RACE_SUMMARY,accepted_a={accepted_a},accepted_b={accepted_b},rejected={rejected},missing={missing},first_generation={},last_generation={}",
        generations[0],
        generations.last().expect("writer generation")
    );
}

/// Each committed Generation has unique bytes. Monotonic read/write intervals
/// show which attempts overlapped a client PUT call, and the SDK's opt-in
/// accepted-ID trace is checked against the returned generation-coded bytes.
#[tokio::test]
#[ignore = "requires two real Rails, striped writes, and CS_RAIL_TIMING=1"]
async fn unique_generations_survive_concurrent_direct_rail_reads() {
    fn segments(bytes: &Bytes) -> Vec<Bytes> {
        const CHUNK: usize = 4 * 1024 * 1024;
        (0..bytes.len())
            .step_by(CHUNK)
            .map(|offset| bytes.slice(offset..(offset + CHUNK).min(bytes.len())))
            .collect()
    }

    assert_eq!(std::env::var("CS_RAIL_TIMING").as_deref(), Ok("1"));
    let coordinator = setting("CS_RAIL_COORDINATOR", "http://127.0.0.1:50051");
    let mib = setting("CS_RAIL_OBJECT_MIB", "8")
        .parse::<usize>()
        .expect("object size in MiB");
    assert!(mib >= 8 && mib % 4 == 0);
    let size = mib * 1024 * 1024;
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("wall clock")
        .as_nanos();
    let key = format!("unique-race-{nanos}");
    let mut seed = KvClient::connect(coordinator.clone())
        .await
        .expect("connect initial writer");
    assert!(seed
        .put_stream_chunks("rail-e2e", &key, segments(&Bytes::from(vec![0x10; size])))
        .await
        .expect("seed generation 1"));
    let initial = seed
        .lookup_object("rail-e2e", &key)
        .await
        .expect("initial lookup")
        .expect("seeded object");
    assert_eq!(initial.descriptor.object_generation, 1);
    assert_eq!(initial.descriptor.size as usize, size);
    assert!(initial.descriptor.is_striped);
    let placement = initial.placement.as_ref().expect("initial placement");
    let advertised = placement
        .chunks
        .first()
        .expect("stripe")
        .rdma_endpoint
        .clone();
    assert!(placement
        .chunks
        .iter()
        .all(|chunk| chunk.rdma_endpoint == advertised));
    println!(
        "UNIQUE_CONFIG,object_key={key},object_mib={mib},stripe_count={},direct_staging=true,put_interval_scope=sdk_call,rejection_id_trace=true",
        initial.descriptor.stripe_count
    );
    let listener0 = setting("CS_RAIL_LISTENER0", &advertised);
    let listener1 = setting("CS_RAIL_LISTENER1", "127.0.0.1:50054");
    let reader = Arc::new(
        RailReader::new(
            vec![
                route(&advertised, 0, &listener0),
                route(&advertised, 1, &listener1),
            ],
            RailLimits {
                direct_staging: true,
                io_timeout: Duration::from_secs(10),
                ..RailLimits::default()
            },
        )
        .expect("direct reader"),
    );
    let mut client = KvClient::connect(coordinator.clone())
        .await
        .expect("connect reader");
    let epoch = Instant::now();
    let writer_key = key.clone();
    let writer = tokio::spawn(async move {
        let mut writer_client = KvClient::connect(coordinator)
            .await
            .expect("connect concurrent writer");
        let mut committed = Vec::new();
        for revision in 0..15u8 {
            let value = 0x20u8 + revision;
            let payload = Bytes::from(vec![value; size]);
            let chunks = segments(&payload);
            let start_ns = epoch.elapsed().as_nanos();
            assert!(writer_client
                .put_stream_chunks("rail-e2e", &writer_key, chunks)
                .await
                .expect("concurrent rewrite"));
            let put_end_ns = epoch.elapsed().as_nanos();
            let current = writer_client
                .lookup_object("rail-e2e", &writer_key)
                .await
                .expect("writer lookup")
                .expect("written object");
            let end_ns = epoch.elapsed().as_nanos();
            let generation = current.descriptor.object_generation;
            assert_eq!(generation, u64::from(revision) + 2);
            println!(
                "UNIQUE_WRITE,revision={revision},generation={generation},byte={value},start_ns={start_ns},put_end_ns={put_end_ns},end_ns={end_ns}"
            );
            committed.push((generation, start_ns, put_end_ns, end_ns));
            tokio::time::sleep(Duration::from_millis(15)).await;
        }
        committed
    });

    let mut accepted_generations = Vec::new();
    let mut read_intervals = Vec::new();
    let mut rejected = 0usize;
    let mut missing = 0usize;
    for attempt in 0..36usize {
        let mut destination = vec![0xA5; size];
        let start_ns = epoch.elapsed().as_nanos();
        let result = tokio::time::timeout(
            Duration::from_secs(30),
            client.read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut destination,
                None,
            ),
        )
        .await
        .expect("bounded direct read");
        let end_ns = epoch.elapsed().as_nanos();
        read_intervals.push((start_ns, end_ns));
        match result {
            Ok(Some(length)) => {
                assert_eq!(length, size);
                let value = destination[0];
                assert!(destination.iter().all(|byte| *byte == value));
                let generation = match value {
                    0x10 => 1,
                    0x20..=0x2E => u64::from(value - 0x20) + 2,
                    _ => panic!("unknown version byte at read attempt {attempt}: {value}"),
                };
                accepted_generations.push(generation);
                println!(
                    "UNIQUE_READ,attempt={attempt},result=accepted,byte={value},generation_inferred={generation},start_ns={start_ns},end_ns={end_ns}"
                );
            }
            Ok(None) => {
                assert!(destination.iter().all(|byte| *byte == 0xA5));
                missing += 1;
                println!(
                    "UNIQUE_READ,attempt={attempt},result=missing,start_ns={start_ns},end_ns={end_ns}"
                );
            }
            Err(error) => {
                assert!(destination.iter().all(|byte| *byte == 0xA5));
                rejected += 1;
                println!(
                    "UNIQUE_READ,attempt={attempt},result=rejected,error={error},start_ns={start_ns},end_ns={end_ns}"
                );
            }
        }
        if writer.is_finished() && attempt >= 19 {
            break;
        }
    }
    let committed = writer.await.expect("writer task joined");
    let put_overlaps = read_intervals
        .iter()
        .filter(|(read_start, read_end)| {
            committed
                .iter()
                .any(|(_, write_start, put_end, _)| read_start < put_end && write_start < read_end)
        })
        .count();
    let round_overlaps = read_intervals
        .iter()
        .filter(|(read_start, read_end)| {
            committed.iter().any(|(_, write_start, _, round_end)| {
                read_start < round_end && write_start < read_end
            })
        })
        .count();
    assert!(put_overlaps > 0, "no read overlapped an SDK PUT call");
    assert!(round_overlaps >= put_overlaps);
    assert!(!accepted_generations.is_empty());
    assert!(
        accepted_generations
            .iter()
            .all(|generation| *generation == 1
                || committed.iter().any(|entry| entry.0 == *generation))
    );

    let mut final_destination = vec![0xA5; size];
    assert_eq!(
        client
            .read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut final_destination,
                None,
            )
            .await
            .expect("post-rewrite direct read"),
        Some(size)
    );
    assert!(final_destination.iter().all(|byte| *byte == 0x2E));
    println!("UNIQUE_FINAL,generation_inferred=16,byte=46,bytes={size}");
    let modes = reader.mode_counts();
    assert_eq!(modes.direct as usize, read_intervals.len() + 1);
    assert_eq!(modes.one + modes.packed_greedy + modes.packed_contiguous, 0);
    println!(
        "UNIQUE_MODE,direct={},one={},packed_greedy={},packed_contiguous={}",
        modes.direct, modes.one, modes.packed_greedy, modes.packed_contiguous
    );
    for rail in reader.snapshots() {
        assert_eq!(rail.inflight_requests, 0);
        assert_eq!(rail.inflight_bytes, 0);
        assert_eq!(rail.registered_bytes, 0);
        assert!(rail.bytes > 0);
        println!(
            "UNIQUE_RAIL,id={},bytes={},reads_ok={},reads_err={},inflight_requests={},inflight_bytes={},registered_bytes={}",
            rail.id,
            rail.bytes,
            rail.reads_ok,
            rail.reads_err,
            rail.inflight_requests,
            rail.inflight_bytes,
            rail.registered_bytes,
        );
    }
    println!(
        "UNIQUE_SUMMARY,attempts={},accepted={},rejected={rejected},missing={missing},put_overlapping={put_overlaps},round_overlapping={round_overlaps},first_generation=1,last_generation=16",
        read_intervals.len(),
        accepted_generations.len(),
    );
}

#[tokio::test]
#[ignore = "requires one reachable HCA or RXE listener and a striped KVService"]
async fn pooled_real_rail_reuses_healthy_connection_and_retires_stale_read() {
    let (mut client, key, payload, advertised) = seeded_object().await;
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &listener)],
            RailLimits {
                rail_cooldown: Duration::ZERO,
                ..RailLimits::default()
            },
        )
        .unwrap()
        .with_connection_pool(RailConnectionPoolOptions {
            max_live_connections: 1,
            configuration_generation: 1,
        })
        .unwrap(),
    );
    for _ in 0..2 {
        let mut destination = vec![0xA5; payload.len()];
        assert_eq!(
            client
                .read_multi_rail_into(
                    Arc::clone(&reader),
                    "rail-e2e",
                    &key,
                    &mut destination,
                    None
                )
                .await
                .expect("pooled real rail read"),
            Some(payload.len())
        );
        assert_eq!(destination, payload);
    }
    let snapshot = reader.connection_pool_snapshot().unwrap();
    assert_eq!(snapshot.created, 1);
    assert_eq!(snapshot.reused, 1);
    assert_eq!(snapshot.idle, 1);
    assert_eq!(snapshot.active, 0);
    let lookup = client
        .lookup_object("rail-e2e", &key)
        .await
        .unwrap()
        .unwrap();
    let mut stale = lookup.descriptor.clone();
    stale.object_generation += 1;
    let mut destination = vec![0xA5; payload.len()];
    assert!(reader
        .read_into(
            &stale,
            lookup.placement.as_ref().unwrap(),
            &mut destination,
            None
        )
        .is_err());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
    let snapshot = reader.connection_pool_snapshot().unwrap();
    assert_eq!(snapshot.retired, 1);
    assert_eq!(snapshot.live, 0);
    assert_eq!(snapshot.idle, 0);
    assert_eq!(
        client
            .read_multi_rail_into(
                Arc::clone(&reader),
                "rail-e2e",
                &key,
                &mut destination,
                None
            )
            .await
            .expect("fresh connection after retired stale read"),
        Some(payload.len())
    );
    assert_eq!(destination, payload);
    let snapshot = reader.connection_pool_snapshot().unwrap();
    assert_eq!(snapshot.created, 2);
    assert_eq!(snapshot.reused, 2);
    assert_eq!(snapshot.retired, 1);
    assert_eq!(snapshot.live, 1);
    assert_eq!(snapshot.idle, 1);
    eprintln!("POOLED_REAL_RAIL_FINAL {snapshot:?}");
    drop(reader);
}

#[tokio::test]
#[ignore = "requires one reachable HCA or RXE listener and a striped KVService"]
async fn pooled_real_rail_parallel_reads_preserve_exclusive_leases() {
    let (client, key, payload, advertised) = seeded_object().await;
    let listener = setting("CS_RAIL_LISTENER0", &advertised);
    let reader = Arc::new(
        RailReader::new(
            vec![route(&advertised, 0, &listener)],
            RailLimits::default(),
        )
        .unwrap()
        .with_connection_pool(RailConnectionPoolOptions {
            max_live_connections: 4,
            configuration_generation: 1,
        })
        .unwrap(),
    );
    for _ in 0..2 {
        let mut reads = Vec::new();
        for _ in 0..4 {
            let mut client = client.clone();
            let reader = Arc::clone(&reader);
            let key = key.clone();
            let size = payload.len();
            reads.push(tokio::spawn(async move {
                let mut destination = vec![0xA5; size];
                let result = client
                    .read_multi_rail_into(reader, "rail-e2e", &key, &mut destination, None)
                    .await;
                (result, destination)
            }));
        }
        for read in reads {
            let (result, destination) = read.await.unwrap();
            assert_eq!(result.expect("concurrent pooled read"), Some(payload.len()));
            assert_eq!(destination, payload);
        }
        let snapshot = reader.connection_pool_snapshot().unwrap();
        assert!(snapshot.created <= 4);
        assert!(snapshot.live <= 4);
        assert_eq!(snapshot.active, 0);
        assert_eq!(snapshot.connecting, 0);
        assert_eq!(snapshot.retiring, 0);
        assert_eq!(snapshot.rejected, 0);
        assert_eq!(snapshot.retired, 0);
        assert_eq!(snapshot.idle, snapshot.live);
    }
    let snapshot = reader.connection_pool_snapshot().unwrap();
    assert_eq!(snapshot.created + snapshot.reused, 8);
    assert!(snapshot.reused >= 4);
    eprintln!("POOLED_REAL_RAIL_PARALLEL_FINAL {snapshot:?}");
}

#[tokio::test]
#[ignore = "requires one reachable HCA or RXE listener and a KVService accepting unstriped objects"]
async fn unstriped_real_read_bypasses_enabled_connection_pool() {
    let mut client = KvClient::connect(setting("CS_RAIL_COORDINATOR", "http://127.0.0.1:50051"))
        .await
        .unwrap();
    let key = format!(
        "pool-unstriped-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let payload: Vec<u8> = (0..8192).map(|offset| (offset % 251) as u8).collect();
    assert!(client.put("rail-e2e", &key, payload.clone()).await.unwrap());
    let lookup = client
        .lookup_object("rail-e2e", &key)
        .await
        .unwrap()
        .unwrap();
    assert!(
        !lookup.descriptor.is_striped,
        "test requires an unstriped object"
    );
    let advertised = &lookup
        .placement
        .as_ref()
        .expect("unstriped placement")
        .chunks[0]
        .rdma_endpoint;
    let listener = setting("CS_RAIL_LISTENER0", advertised);
    let reader = Arc::new(
        RailReader::new(vec![route(advertised, 0, &listener)], RailLimits::default())
            .unwrap()
            .with_connection_pool(RailConnectionPoolOptions {
                max_live_connections: 1,
                configuration_generation: 1,
            })
            .unwrap(),
    );
    for _ in 0..2 {
        let mut destination = vec![0xA5; payload.len()];
        assert_eq!(
            client
                .read_multi_rail_into(
                    Arc::clone(&reader),
                    "rail-e2e",
                    &key,
                    &mut destination,
                    None
                )
                .await
                .expect("legacy unstriped read with pool configured"),
            Some(payload.len())
        );
        assert_eq!(destination, payload);
        assert_eq!(
            reader.connection_pool_snapshot().unwrap(),
            Default::default(),
            "unstriped requests must not acquire or create pooled connections"
        );
    }
    eprintln!(
        "UNSTRIPED_POOL_BYPASS_FINAL {:?}",
        reader.connection_pool_snapshot().unwrap()
    );
}

// These test-only TCP relays preserve actual Verbs handshakes and DMA. They
// delay control frames, never inject a provider completion or return value.
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

fn recovery_relay_accept(listener: &std::net::TcpListener) -> std::io::Result<std::net::TcpStream> {
    let until = Instant::now() + Duration::from_secs(90);
    loop {
        match listener.accept() {
            Ok((stream, _)) => return Ok(stream),
            Err(error)
                if error.kind() == std::io::ErrorKind::WouldBlock && Instant::now() < until =>
            {
                std::thread::sleep(Duration::from_millis(5));
            }
            Err(error) => return Err(error),
        }
    }
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

#[derive(Debug)]
struct RecoveryRelayReceipt {
    frame: RecoveryFrame,
    client_qpn: u32,
    server_peer: String,
    forwarded: Instant,
    terminal: Instant,
    terminal_kind: &'static str,
}

async fn real_recovery_after_late_request_case(direct_staging: bool, pooled: bool) {
    use std::io::{Read, Write};
    use std::net::{Shutdown, TcpListener};
    use std::sync::mpsc;
    let (mut client, key, payload, advertised) = seeded_object().await;
    let initial = client
        .lookup_object("rail-e2e", &key)
        .await
        .unwrap()
        .unwrap();
    let placement = initial.placement.as_ref().unwrap();
    let failed_listener = TcpListener::bind("127.0.0.1:0").unwrap();
    let healthy_listener = TcpListener::bind("127.0.0.1:0").unwrap();
    failed_listener.set_nonblocking(true).unwrap();
    healthy_listener.set_nonblocking(true).unwrap();
    let failed_endpoint = failed_listener.local_addr().unwrap().to_string();
    let healthy_endpoint = healthy_listener.local_addr().unwrap().to_string();
    let real_failed = setting("CS_RAIL_LISTENER0", &advertised);
    let real_healthy = setting("CS_RAIL_LISTENER1", "10.82.1.1:25354");
    let (retry_registered_tx, retry_registered_rx) = mpsc::channel();
    let (old_terminal_tx, old_terminal_rx) = mpsc::channel();
    let started = Instant::now();
    let failed_key = key.clone();
    let failed_relay = std::thread::spawn(move || -> anyhow::Result<RecoveryRelayReceipt> {
        let mut downstream = recovery_relay_accept(&failed_listener)?;
        let (mut upstream, client_qpn) = recovery_relay_handshake(&mut downstream, &real_failed)?;
        let frame = receive_recovery_frame(&mut downstream)?.expect("first failed-path request");
        let server_peer = upstream.local_addr()?.to_string();
        // Preserve the real frame even if the evidence gate later rejects a
        // stale/not-found ACK, reset, timeout, or another unexpected terminal.
        eprintln!("RECOVERY_RXE_OLD_REQUEST direct={direct_staging} pooled={pooled} key={failed_key} server_peer={server_peer} qpn={client_qpn} frame={frame:?}");
        downstream.shutdown(Shutdown::Both)?;
        // The client observes a typed UnexpectedEof.
        drop(downstream);
        // The healthy relay signals only after receiving the retry's complete
        // SGE frame. Its target MR is already registered and remains alive.
        retry_registered_rx.recv_timeout(Duration::from_secs(90))?;
        let forwarded = Instant::now();
        upstream.write_all(&frame.wire)?;
        let mut reply = [0; 14];
        let terminal_kind = match upstream.read(&mut reply[..1]) {
            Ok(0) => "eof",
            Ok(1) => {
                upstream.read_exact(&mut reply[1..])?;
                eprintln!("RECOVERY_RXE_REJECTED_OLD_TERMINAL key={failed_key} server_peer={server_peer} reply={reply:02x?}");
                anyhow::bail!("INCONCLUSIVE: an old-request ACK does not meet this late-WRITE-attempt experiment's expected EOF gate");
            }
            Ok(_) => unreachable!(),
            Err(error) => {
                return Err(anyhow::anyhow!(
                    "INCONCLUSIVE: expected old-server EOF, observed {error}"
                ))
            } // Reset/timeout is not this experiment's evidence.
        };
        let terminal = Instant::now();
        old_terminal_tx.send(())?;
        Ok(RecoveryRelayReceipt {
            frame,
            client_qpn,
            server_peer,
            forwarded,
            terminal,
            terminal_kind,
        })
    });
    let healthy_relay = std::thread::spawn(move || -> anyhow::Result<Vec<RecoveryRelayReceipt>> {
        let mut receipts = Vec::new();
        while receipts.len() < 2 {
            let mut downstream = recovery_relay_accept(&healthy_listener)?;
            let (mut upstream, client_qpn) =
                recovery_relay_handshake(&mut downstream, &real_healthy)?;
            while let Some(frame) = receive_recovery_frame(&mut downstream)? {
                if receipts.len() == 1 {
                    retry_registered_tx.send(())?;
                    old_terminal_rx.recv_timeout(Duration::from_secs(90))?;
                }
                let forwarded = Instant::now();
                upstream.write_all(&frame.wire)?;
                let mut reply = [0; 14];
                upstream.read_exact(&mut reply)?;
                anyhow::ensure!(
                    reply[0] == 3 && reply[1] == 1,
                    "healthy path needs a full success ACK"
                );
                downstream.write_all(&reply)?;
                receipts.push(RecoveryRelayReceipt {
                    frame,
                    client_qpn,
                    server_peer: upstream.local_addr()?.to_string(),
                    forwarded,
                    terminal: Instant::now(),
                    terminal_kind: "success_ack",
                });
                if receipts.len() == 2 {
                    return Ok(receipts);
                }
            }
        }
        Ok(receipts)
    });
    let mut reader = RailReader::new(
        vec![
            route(&advertised, 0, &failed_endpoint),
            route(&advertised, 1, &healthy_endpoint),
        ],
        RailLimits {
            direct_staging,
            io_timeout: Duration::from_secs(90),
            ..RailLimits::default()
        },
    )
    .unwrap()
    .with_recovery(RailRecoveryOptions {
        timeout: Duration::from_secs(120),
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
    let assignment = reader
        .describe_assignment(&initial.descriptor, placement)
        .unwrap();
    let failed_task = assignment.iter().find(|task| task.id == "rail0").unwrap();
    let healthy_task = assignment.iter().find(|task| task.id == "rail1").unwrap();
    let reader = Arc::new(reader);
    let mut destination = vec![0xA5; payload.len()];
    let result = client
        .read_multi_rail_into(
            Arc::clone(&reader),
            "rail-e2e",
            &key,
            &mut destination,
            None,
        )
        .await;
    let failed_receipt = failed_relay.join().expect("failed-path relay panicked");
    let healthy_receipts = healthy_relay.join().expect("healthy-path relay panicked");
    assert!(
        failed_receipt.is_ok() && healthy_receipts.is_ok(),
        "relay outcomes: failed={failed_receipt:?}, healthy={healthy_receipts:?}"
    );
    let failed_receipt = failed_receipt.unwrap();
    let healthy_receipts = healthy_receipts.unwrap();
    assert_eq!(result.expect("bounded real recovery"), Some(payload.len()));
    assert_eq!(destination, payload);
    let current = client
        .lookup_object("rail-e2e", &key)
        .await
        .unwrap()
        .unwrap();
    assert_eq!(current.descriptor, initial.descriptor);
    assert_eq!(current.placement, initial.placement);
    assert_eq!(failed_receipt.frame.stripes, failed_task.stripe_indices);
    assert_eq!(
        healthy_receipts[0].frame.stripes,
        healthy_task.stripe_indices
    );
    assert_eq!(
        healthy_receipts[1].frame.stripes,
        failed_task.stripe_indices
    );
    assert!(healthy_receipts[1].frame.captured <= failed_receipt.forwarded);
    assert!(failed_receipt.terminal <= healthy_receipts[1].forwarded);
    if direct_staging {
        for stripe in &failed_task.stripe_indices {
            assert_eq!(
                failed_receipt.frame.segments[*stripe as usize].0,
                healthy_receipts[1].frame.segments[*stripe as usize].0,
                "direct retry must reuse the failed private range after old QP retirement"
            );
        }
    }
    let recovery = reader.recovery_snapshot();
    assert_eq!(recovery.retry_tasks, 1);
    assert_eq!(recovery.requested_payload_bytes, failed_task.bytes as u64);
    assert_eq!(recovery.successful_tasks, 1);
    let rails = reader.snapshots();
    assert_eq!(rails[0].reads_err, 1);
    assert_eq!(rails[1].reads_ok, 2);
    assert_eq!(rails[1].bytes, payload.len() as u64);
    assert!(rails.iter().all(|rail| rail.inflight_requests == 0
        && rail.inflight_bytes == 0
        && rail.registered_bytes == 0));
    if pooled {
        let pool = reader.connection_pool_snapshot().unwrap();
        assert_eq!(pool.created, 2);
        assert_eq!(pool.reused, 1);
        assert_eq!(pool.retired, 1);
        assert_eq!(pool.active, 0);
        assert_eq!(pool.retiring, 0);
    }
    for (name, receipt) in [
        ("failed", &failed_receipt),
        ("healthy_initial", &healthy_receipts[0]),
        ("healthy_retry", &healthy_receipts[1]),
    ] {
        eprintln!("RECOVERY_RXE_FRAME direct={direct_staging} pooled={pooled} key={key} role={name} qpn={} server_peer={} stripes={:?} segments={:?} captured_us={} forwarded_us={} terminal_us={} terminal={} wire={:02x?}",
            receipt.client_qpn, receipt.server_peer, receipt.frame.stripes, receipt.frame.segments,
            receipt.frame.captured.duration_since(started).as_micros(), receipt.forwarded.duration_since(started).as_micros(),
            receipt.terminal.duration_since(started).as_micros(), receipt.terminal_kind, receipt.frame.wire);
    }
    eprintln!("RECOVERY_RXE_RESULT direct={direct_staging} pooled={pooled} payload_bytes={} payload_xxh3={:016x} retry_tasks={} requested_retry_payload={} same_descriptor=true caller_payload_exact=true elapsed_ms={} old_terminal={} proof=late_control_request_after_retry_target_registration",
        payload.len(), twox_hash::xxh3::hash64(&destination), recovery.retry_tasks, recovery.requested_payload_bytes,
        started.elapsed().as_millis(), failed_receipt.terminal_kind);
    // All old requests have a real terminal observation before buffer reuse.
    destination.fill(0x5A);
    assert!(destination.iter().all(|byte| *byte == 0x5A));
}

#[tokio::test]
#[ignore = "requires isolated dual RXE/HCA paths; delays a real old request until retry MR registration"]
async fn recovery_real_late_request_after_retry_registration_packed_unpooled() {
    real_recovery_after_late_request_case(false, false).await;
}

#[tokio::test]
#[ignore = "requires isolated dual RXE/HCA paths; delays a real old request until retry MR registration"]
async fn recovery_real_late_request_after_retry_registration_packed_pooled() {
    real_recovery_after_late_request_case(false, true).await;
}

#[tokio::test]
#[ignore = "requires isolated dual RXE/HCA paths; delays a real old request until retry MR registration"]
async fn recovery_real_late_request_after_retry_registration_direct_unpooled() {
    real_recovery_after_late_request_case(true, false).await;
}

#[tokio::test]
#[ignore = "requires isolated dual RXE/HCA paths; delays a real old request until retry MR registration"]
async fn recovery_real_late_request_after_retry_registration_direct_pooled() {
    real_recovery_after_late_request_case(true, true).await;
}
