use super::connection_pool::finish_transfer;
use super::*;
use crate::pb;
use std::sync::Mutex;

// This private transport seam exercises scheduling/publication, not Verbs.
struct RecoveringMock {
    object: Vec<u8>,
    calls: Mutex<Vec<(String, Vec<u32>)>>,
}

impl RailTransport for RecoveringMock {
    fn fetch(
        &self,
        route: &RailRoute,
        task: &RailTask,
        _descriptor: &pb::ObjectDescriptor,
        _timeout: Duration,
    ) -> Result<Vec<u8>, RailReadError> {
        self.calls.lock().unwrap().push((
            route.id.clone(),
            task.stripes.iter().map(|stripe| stripe.index).collect(),
        ));
        if route.id == "rail0" {
            return Err(connection_pool::mock_quiescent_failure());
        }
        let mut packed = vec![0; task.packed_len];
        for stripe in &task.stripes {
            packed[stripe.packed_offset..stripe.packed_offset + stripe.length].copy_from_slice(
                &self.object[stripe.object_offset..stripe.object_offset + stripe.length],
            );
        }
        Ok(packed)
    }
}

#[test]
fn recovery_retries_only_failed_stripes_in_packed_and_direct_modes() {
    for direct_staging in [false, true] {
        let object: Vec<u8> = (0..64).collect();
        let mock = RecoveringMock {
            object: object.clone(),
            calls: Mutex::new(Vec::new()),
        };
        let reader = RailReader::new(
            routes(),
            RailLimits {
                direct_staging,
                ..RailLimits::default()
            },
        )
        .unwrap()
        .with_recovery(RailRecoveryOptions {
            timeout: Duration::from_secs(5),
        })
        .unwrap();
        let (descriptor, placement) = fixture(64, 8);
        let mut destination = vec![0xA5; 64];
        assert_eq!(
            reader
                .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
                .unwrap(),
            64
        );
        assert_eq!(destination, object);
        let calls = mock.calls.lock().unwrap();
        assert_eq!(calls.len(), 3);
        assert_eq!(
            reader.recovery_snapshot(),
            RailRecoverySnapshot {
                retry_tasks: 1,
                requested_payload_bytes: 32,
                successful_tasks: 1
            }
        );
        let failed = &calls.iter().find(|(rail, _)| rail == "rail0").unwrap().1;
        assert_eq!(
            calls
                .iter()
                .filter(|(_, stripes)| stripes == failed)
                .count(),
            2
        );
        assert_eq!(
            calls
                .iter()
                .filter(|(_, stripes)| stripes != failed)
                .count(),
            1
        );
        let budget = reader.budget.lock().unwrap();
        assert_eq!(budget.active_reads, 0);
        assert_eq!(
            budget.staging_bytes + budget.registered_bytes + budget.inflight_bytes,
            0
        );
        assert!(budget.rail_active_reads.iter().all(|count| *count == 0));
        assert!(budget.rail_inflight_bytes.iter().all(|bytes| *bytes == 0));
    }
}

struct MockTransport {
    object: Vec<u8>,
    calls: Mutex<Vec<String>>,
    failing_rail: Option<String>,
}

impl MockTransport {
    fn new(object: Vec<u8>) -> Self {
        Self {
            object,
            calls: Mutex::new(Vec::new()),
            failing_rail: None,
        }
    }
}

impl RailTransport for MockTransport {
    fn fetch(
        &self,
        route: &RailRoute,
        task: &RailTask,
        _descriptor: &pb::ObjectDescriptor,
        _timeout: std::time::Duration,
    ) -> Result<Vec<u8>, RailReadError> {
        self.calls
            .lock()
            .unwrap()
            .push(route.connection.endpoint.clone());
        let mut packed = vec![0u8; task.packed_len];
        for stripe in &task.stripes {
            packed[stripe.packed_offset..stripe.packed_offset + stripe.length].copy_from_slice(
                &self.object[stripe.object_offset..stripe.object_offset + stripe.length],
            );
        }
        if self.failing_rail.as_deref() == Some(&route.id) {
            return Err(RailReadError::Transport("injected disconnect".into()));
        }
        Ok(packed)
    }
}

fn fixture(size: usize, chunk_size: usize) -> (pb::ObjectDescriptor, pb::PlacementDescriptor) {
    let count = size.div_ceil(chunk_size);
    let descriptor = pb::ObjectDescriptor {
        key: Some(pb::ObjectKey {
            namespace: "test".into(),
            object_key: "obj".into(),
        }),
        object_handle: "handle".into(),
        object_generation: 1,
        content_etag: "etag".into(),
        layout_version: 1,
        size: size as u64,
        is_striped: true,
        stripe_count: count as u32,
        chunk_size: chunk_size as u64,
    };
    let placement = pb::PlacementDescriptor {
        key: descriptor.key.clone(),
        chunks: (0..count)
            .map(|index| pb::PlacementChunk {
                stripe_index: index as u32,
                node_id: "node-a".into(),
                grpc_endpoint: "10.0.0.1:50051".into(),
                rdma_endpoint: "10.0.0.1:50053".into(),
                device_id: 0,
                storage_handle: format!("stripe-{index}"),
                offset: (index * chunk_size) as u64,
                length: (size - index * chunk_size).min(chunk_size) as u64,
                checksum: String::new(),
            })
            .collect(),
        ..Default::default()
    };
    (descriptor, placement)
}

fn routes() -> Vec<RailRoute> {
    vec![
        RailRoute::new(
            "rail0",
            "10.0.0.1:50053",
            crate::rdma::RdmaClientConfig::new("10.0.0.1:50053", "mock0"),
        ),
        RailRoute::new(
            "rail1",
            "10.0.0.1:50053",
            crate::rdma::RdmaClientConfig::new("10.0.1.1:50054", "mock1"),
        ),
    ]
}

#[test]
fn two_independent_listeners_restore_one_unmodified_placement() {
    let bytes: Vec<u8> = (0..64).map(|index| index as u8).collect();
    let mock = MockTransport::new(bytes.clone());
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert_eq!(
        reader
            .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            .unwrap(),
        64
    );
    assert_eq!(destination, bytes);
    let calls = mock.calls.lock().unwrap();
    assert_eq!(calls.len(), 2);
    assert!(calls.contains(&"10.0.0.1:50053".to_string()));
    assert!(calls.contains(&"10.0.1.1:50054".to_string()));
    let snapshots = reader.snapshots();
    assert_eq!(snapshots[0].bytes, 32);
    assert_eq!(snapshots[1].bytes, 32);
    assert!(snapshots.iter().all(|rail| rail.inflight_requests == 0));
    assert!(snapshots.iter().all(|rail| rail.registered_bytes == 0));
    assert_eq!(snapshots[0].peak_inflight_bytes, 32);
    assert!(snapshots.iter().all(|rail| rail.peak_registered_bytes > 0));
}

#[test]
fn direct_staging_assigns_disjoint_ordered_slices_and_restores_the_object() {
    struct DirectMock {
        object: Vec<u8>,
        targets: Mutex<Vec<(Vec<u32>, usize, usize)>>,
    }
    impl RailTransport for DirectMock {
        fn fetch(
            &self,
            _route: &RailRoute,
            _task: &RailTask,
            _descriptor: &pb::ObjectDescriptor,
            _timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            panic!("direct staging must receive into the private ordered target")
        }

        fn fetch_into(
            &self,
            _route: &RailRoute,
            task: &RailTask,
            _descriptor: &pb::ObjectDescriptor,
            target: &mut [u8],
            _timeout: Duration,
        ) -> Result<(), RailReadError> {
            assert_eq!(target.len(), task.packed_len);
            self.targets.lock().unwrap().push((
                task.stripes.iter().map(|stripe| stripe.index).collect(),
                target.as_ptr() as usize,
                target.len(),
            ));
            for stripe in &task.stripes {
                target[stripe.packed_offset..stripe.packed_offset + stripe.length].copy_from_slice(
                    &self.object[stripe.object_offset..stripe.object_offset + stripe.length],
                );
            }
            Ok(())
        }
    }

    let object: Vec<u8> = (0..64).collect();
    let mock = DirectMock {
        object: object.clone(),
        targets: Mutex::new(Vec::new()),
    };
    let reader = RailReader::new(
        routes(),
        RailLimits {
            direct_staging: true,
            ..RailLimits::default()
        },
    )
    .unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let staged = reader
        .read_staged_with(&descriptor, &placement, &mock, None)
        .unwrap();
    assert_eq!(staged.as_bytes(), object);
    assert_eq!(reader.mode_counts().direct, 1);
    let mut targets = mock.targets.lock().unwrap().clone();
    targets.sort_by_key(|(_, address, _)| *address);
    let base = staged.as_bytes().as_ptr() as usize;
    assert_eq!(
        targets,
        vec![
            (vec![0, 1, 2, 3], base, 32),
            (vec![4, 5, 6, 7], base + 32, 32)
        ]
    );
}

#[test]
fn direct_staging_sge_uses_the_owned_slice_and_separate_dummy_mr() {
    let (descriptor, placement) = fixture(64, 8);
    let plan = RailPlan::build(&descriptor, &placement, &routes())
        .unwrap()
        .contiguous_for_single_owner(&placement, &routes(), true);
    assert!(plan.direct_staging);
    let task = &plan.tasks[0];
    let target = SgeRegion {
        addr: 1000,
        rkey: 11,
        len: 32,
    };
    let dummy = SgeRegion {
        addr: 2000,
        rkey: 22,
        len: 8,
    };
    let segments = build_sge_segments_direct(task, &descriptor, target, dummy).unwrap();
    assert_eq!(segments.len(), 8);
    assert_eq!(segments[0], (1000, 11, 8));
    assert_eq!(segments[3], (1024, 11, 8));
    assert_eq!(segments[4], (2000, 22, 8));
    assert_eq!(segments[7], (2000, 22, 8));
    assert!(
        build_sge_segments_direct(task, &descriptor, target, SgeRegion { len: 7, ..dummy })
            .is_err()
    );
}

#[test]
fn direct_staging_partial_second_rail_failure_never_publishes() {
    struct PartialWrite;
    impl RailTransport for PartialWrite {
        fn fetch(
            &self,
            _route: &RailRoute,
            _task: &RailTask,
            _descriptor: &pb::ObjectDescriptor,
            _timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            panic!("direct path must not use packed receive buffers")
        }

        fn fetch_into(
            &self,
            route: &RailRoute,
            _task: &RailTask,
            _descriptor: &pb::ObjectDescriptor,
            target: &mut [u8],
            _timeout: Duration,
        ) -> Result<(), RailReadError> {
            target.fill(0x42);
            if route.id == "rail1" {
                return Err(RailReadError::Transport(
                    "injected second-Rail failure".into(),
                ));
            }
            Ok(())
        }
    }

    let reader = RailReader::new(
        routes(),
        RailLimits {
            direct_staging: true,
            ..RailLimits::default()
        },
    )
    .unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(
            &descriptor,
            &placement,
            &mut destination,
            &PartialWrite,
            None
        ),
        Err(RailReadError::Transport(_))
    ));
    assert_eq!(destination, vec![0xA5; 64]);
    assert_eq!(reader.budget.lock().unwrap().active_reads, 0);
    assert!(reader
        .snapshots()
        .iter()
        .all(|rail| rail.inflight_requests == 0 && rail.registered_bytes == 0));
}

#[test]
fn direct_staging_reserves_one_object_buffer_and_keeps_it_until_drop() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(
        routes(),
        RailLimits {
            direct_staging: true,
            max_active_reads: 2,
            max_staging_bytes: 80,
            ..RailLimits::default()
        },
    )
    .unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let staged = reader
        .read_staged_with(&descriptor, &placement, &mock, None)
        .expect("one 64-byte object plus two 8-byte dummies fits exactly");
    assert_eq!(staged.as_bytes(), vec![0x42; 64]);
    assert_eq!(reader.budget.lock().unwrap().staging_bytes, 64);
    assert!(matches!(
        reader.read_staged_with(&descriptor, &placement, &mock, None),
        Err(RailReadError::ResourceExhausted(_))
    ));
    drop(staged);
    assert_eq!(reader.budget.lock().unwrap().staging_bytes, 0);
    assert!(reader
        .read_staged_with(&descriptor, &placement, &mock, None)
        .is_ok());
}

#[test]
fn direct_staging_weighted_ranges_and_multi_owner_fallback_are_explicit() {
    let (descriptor, placement) = fixture(64, 8);
    let mut weighted = routes();
    weighted[0].weight = 3;
    let plan = RailPlan::build(&descriptor, &placement, &weighted)
        .unwrap()
        .contiguous_for_single_owner(&placement, &weighted, true);
    assert!(plan.direct_staging);
    assert_eq!(
        plan.tasks
            .iter()
            .map(|task| task
                .stripes
                .iter()
                .map(|stripe| stripe.index)
                .collect::<Vec<_>>())
            .collect::<Vec<_>>(),
        vec![vec![0, 1, 2, 3, 4, 5], vec![6, 7]]
    );

    let (_, mut different_owners) = fixture(64, 8);
    for chunk in &mut different_owners.chunks[4..] {
        chunk.node_id = "node-b".into();
        chunk.rdma_endpoint = "10.0.0.2:50053".into();
    }
    let owner_routes = vec![
        routes()[0].clone(),
        RailRoute::new(
            "node-b",
            "10.0.0.2:50053",
            crate::rdma::RdmaClientConfig::new("10.0.0.2:50053", "mock1"),
        ),
    ];
    let plan = RailPlan::build(&descriptor, &different_owners, &owner_routes)
        .unwrap()
        .contiguous_for_single_owner(&different_owners, &owner_routes, true);
    assert!(!plan.direct_staging);
    let (_, mut ambiguous_owner) = fixture(64, 8);
    for chunk in &mut ambiguous_owner.chunks[4..] {
        chunk.node_id = "node-b".into();
    }
    let plan = RailPlan::build(&descriptor, &ambiguous_owner, &routes())
        .unwrap()
        .contiguous_for_single_owner(&ambiguous_owner, &routes(), true);
    assert!(!plan.direct_staging);
    let (_, mut missing_owner) = fixture(64, 8);
    for chunk in &mut missing_owner.chunks {
        chunk.node_id.clear();
    }
    let plan = RailPlan::build(&descriptor, &missing_owner, &routes())
        .unwrap()
        .contiguous_for_single_owner(&missing_owner, &routes(), true);
    assert!(!plan.direct_staging);
    let reader = RailReader::new(
        owner_routes,
        RailLimits {
            direct_staging: true,
            ..RailLimits::default()
        },
    )
    .unwrap();
    let object: Vec<u8> = (0..64).collect();
    let mock = MockTransport::new(object.clone());
    let mut destination = vec![0xA5; 64];
    reader
        .read_into_with(
            &descriptor,
            &different_owners,
            &mut destination,
            &mock,
            None,
        )
        .unwrap();
    assert_eq!(destination, object);
}

#[test]
fn packed_contiguous_control_uses_the_same_rail_ranges_as_direct_staging() {
    let (descriptor, placement) = fixture(64, 8);
    let configured = routes();
    let packed = RailPlan::build(&descriptor, &placement, &configured)
        .unwrap()
        .contiguous_for_single_owner(&placement, &configured, false);
    let direct = RailPlan::build(&descriptor, &placement, &configured)
        .unwrap()
        .contiguous_for_single_owner(&placement, &configured, true);
    assert!(!packed.direct_staging);
    assert!(direct.direct_staging);
    assert_eq!(
        packed
            .tasks
            .iter()
            .map(|task| task
                .stripes
                .iter()
                .map(|stripe| stripe.index)
                .collect::<Vec<_>>())
            .collect::<Vec<_>>(),
        direct
            .tasks
            .iter()
            .map(|task| task
                .stripes
                .iter()
                .map(|stripe| stripe.index)
                .collect::<Vec<_>>())
            .collect::<Vec<_>>()
    );
    let reader = RailReader::new(
        configured,
        RailLimits {
            contiguous_schedule: true,
            ..RailLimits::default()
        },
    )
    .unwrap();
    let assignments = reader.describe_assignment(&descriptor, &placement).unwrap();
    assert_eq!(assignments[0].stripe_indices, vec![0, 1, 2, 3]);
    assert_eq!(assignments[1].stripe_indices, vec![4, 5, 6, 7]);
    let object: Vec<u8> = (0..64).collect();
    let mock = MockTransport::new(object.clone());
    let mut destination = vec![0xA5; 64];
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    assert_eq!(destination, object);
    assert_eq!(reader.mode_counts().packed_contiguous, 1);
}

#[test]
fn direct_staging_checksum_failure_preserves_caller_sentinel() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(
        routes(),
        RailLimits {
            direct_staging: true,
            ..RailLimits::default()
        },
    )
    .unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    for chunk in &mut placement.chunks {
        chunk.checksum = "0000000000000000".into();
    }
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
        Err(RailReadError::Checksum { stripe: 0 })
    ));
    assert_eq!(destination, vec![0xA5; 64]);
}

#[test]
fn one_complete_rail_hands_off_its_receive_allocation_without_reassembly() {
    struct ReceiveAllocation {
        bytes: Vec<u8>,
        address: Mutex<Option<usize>>,
    }
    impl RailTransport for ReceiveAllocation {
        fn fetch(
            &self,
            _route: &RailRoute,
            task: &RailTask,
            _descriptor: &pb::ObjectDescriptor,
            _timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            assert_eq!(task.packed_len, self.bytes.len());
            let received = self.bytes.clone();
            *self.address.lock().unwrap() = Some(received.as_ptr() as usize);
            Ok(received)
        }
    }

    let bytes: Vec<u8> = (0..64).collect();
    let transport = ReceiveAllocation {
        bytes: bytes.clone(),
        address: Mutex::new(None),
    };
    let reader = RailReader::new(vec![routes()[0].clone()], RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    for chunk in &mut placement.chunks {
        let start = chunk.offset as usize;
        let end = start + chunk.length as usize;
        chunk.checksum = format!("{:016x}", twox_hash::xxh3::hash64(&bytes[start..end]));
    }
    let staged = reader
        .read_staged_with(&descriptor, &placement, &transport, None)
        .unwrap();
    assert_eq!(staged.as_bytes(), bytes);
    assert_eq!(
        staged.as_bytes().as_ptr() as usize,
        transport.address.lock().unwrap().unwrap()
    );
}

#[test]
fn one_rail_checksum_failure_preserves_caller_buffer() {
    let mock = MockTransport::new((0..64).collect());
    let reader = RailReader::new(vec![routes()[0].clone()], RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    for chunk in &mut placement.chunks {
        chunk.checksum = "0000000000000000".into();
    }
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
        Err(RailReadError::Checksum { stripe: 0 })
    ));
    assert_eq!(destination, vec![0xA5; 64]);
}

#[test]
fn advertised_rails_resolve_local_fabrics_and_restore_one_object() {
    let bytes: Vec<u8> = (0..64).map(|index| index as u8).collect();
    let mock = MockTransport::new(bytes.clone());
    let (descriptor, mut placement) = fixture(64, 8);
    placement.rdma_rails = vec![
        pb::RdmaRailEndpoint {
            node_id: "node-a".into(),
            advertised_endpoint: "10.0.0.1:50053".into(),
            fabric_id: "fabric-a".into(),
            listener_endpoint: "10.0.0.1:50053".into(),
        },
        pb::RdmaRailEndpoint {
            node_id: "node-a".into(),
            advertised_endpoint: "10.0.0.1:50053".into(),
            fabric_id: "fabric-b".into(),
            listener_endpoint: "10.0.1.1:50054".into(),
        },
    ];
    let local = vec![
        LocalRailPath::new("rail0", "fabric-a", "mock0"),
        LocalRailPath::new("rail1", "fabric-b", "mock1"),
    ];
    let reader = RailReader::discover_from_placement(&placement, &local, RailLimits::default())
        .expect("two discovered rails");
    assert_eq!(reader.snapshots().len(), 2);
    assert_eq!(reader.snapshots()[1].listener, "10.0.1.1:50054");
    let mut destination = vec![0xA5; 64];
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    assert_eq!(destination, bytes);
    assert_eq!(reader.snapshots()[0].bytes, 32);
    assert_eq!(reader.snapshots()[1].bytes, 32);
}

#[test]
fn discovery_rejects_a_capability_for_another_owner() {
    let (_descriptor, mut placement) = fixture(64, 8);
    placement.rdma_rails = vec![pb::RdmaRailEndpoint {
        node_id: "unexpected-owner".into(),
        advertised_endpoint: "10.0.0.1:50053".into(),
        fabric_id: "fabric-a".into(),
        listener_endpoint: "10.0.0.1:50053".into(),
    }];
    assert!(RailReader::discover_from_placement(
        &placement,
        &[LocalRailPath::new("rail0", "fabric-a", "mock0")],
        RailLimits::default(),
    )
    .is_err());
}

#[test]
fn discovery_maps_one_local_fabric_to_each_actual_storage_owner() {
    let bytes: Vec<u8> = (0..16).map(|index| index as u8).collect();
    let mock = MockTransport::new(bytes.clone());
    let (descriptor, mut placement) = fixture(16, 8);
    placement.chunks[1].node_id = "node-b".into();
    placement.chunks[1].rdma_endpoint = "10.0.0.2:50053".into();
    placement.rdma_rails = vec![
        pb::RdmaRailEndpoint {
            node_id: "node-a".into(),
            advertised_endpoint: "10.0.0.1:50053".into(),
            fabric_id: "fabric-a".into(),
            listener_endpoint: "10.0.0.1:50053".into(),
        },
        pb::RdmaRailEndpoint {
            node_id: "node-b".into(),
            advertised_endpoint: "10.0.0.2:50053".into(),
            fabric_id: "fabric-a".into(),
            listener_endpoint: "10.0.0.2:50053".into(),
        },
    ];
    let reader = RailReader::discover_from_placement(
        &placement,
        &[LocalRailPath::new("rail0", "fabric-a", "mock0")],
        RailLimits::default(),
    )
    .unwrap();
    assert_eq!(reader.routes().len(), 2);
    let mut destination = vec![0xA5; 16];
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    assert_eq!(destination, bytes);
    let calls = mock.calls.lock().unwrap();
    assert!(calls.contains(&"10.0.0.1:50053".to_string()));
    assert!(calls.contains(&"10.0.0.2:50053".to_string()));
}

#[test]
fn discovery_rejects_duplicate_fabrics_and_legacy_absence() {
    let (_descriptor, mut placement) = fixture(16, 8);
    let local = [LocalRailPath::new("rail0", "fabric-a", "mock0")];
    assert!(
        RailReader::discover_from_placement(&placement, &local, RailLimits::default()).is_err()
    );
    let endpoint = pb::RdmaRailEndpoint {
        node_id: "node-a".into(),
        advertised_endpoint: "10.0.0.1:50053".into(),
        fabric_id: "fabric-a".into(),
        listener_endpoint: "10.0.0.1:50053".into(),
    };
    placement.rdma_rails = vec![endpoint.clone(), endpoint];
    assert!(
        RailReader::discover_from_placement(&placement, &local, RailLimits::default()).is_err()
    );
}

#[test]
fn discovered_reader_rejects_a_changed_listener_before_transport() {
    let (descriptor, mut placement) = fixture(16, 8);
    placement.rdma_rails = vec![pb::RdmaRailEndpoint {
        node_id: "node-a".into(),
        advertised_endpoint: "10.0.0.1:50053".into(),
        fabric_id: "fabric-a".into(),
        listener_endpoint: "10.0.0.1:50053".into(),
    }];
    let reader = RailReader::discover_from_placement(
        &placement,
        &[LocalRailPath::new("rail0", "fabric-a", "mock0")],
        RailLimits::default(),
    )
    .unwrap();
    placement.rdma_rails[0].listener_endpoint = "10.0.1.1:50054".into();
    let mock = MockTransport::new(vec![0x42; 16]);
    let mut destination = vec![0xA5; 16];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    assert!(mock.calls.lock().unwrap().is_empty());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

#[test]
fn discovered_reader_rejects_a_changed_storage_owner_before_transport() {
    let (descriptor, mut placement) = fixture(16, 8);
    placement.rdma_rails = vec![pb::RdmaRailEndpoint {
        node_id: "node-a".into(),
        advertised_endpoint: "10.0.0.1:50053".into(),
        fabric_id: "fabric-a".into(),
        listener_endpoint: "10.0.0.1:50053".into(),
    }];
    let reader = RailReader::discover_from_placement(
        &placement,
        &[LocalRailPath::new("rail0", "fabric-a", "mock0")],
        RailLimits::default(),
    )
    .unwrap();
    placement.chunks[1].node_id = "unexpected-owner".into();
    let mock = MockTransport::new(vec![0x42; 16]);
    let mut destination = vec![0xA5; 16];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    assert!(mock.calls.lock().unwrap().is_empty());
    assert!(destination.iter().all(|byte| *byte == 0xA5));
}

#[test]
fn discovery_rejects_one_listener_claimed_by_two_owners() {
    let (_descriptor, mut placement) = fixture(16, 8);
    placement.chunks[1].node_id = "node-b".into();
    placement.chunks[1].rdma_endpoint = "10.0.0.2:50053".into();
    placement.rdma_rails = vec![
        pb::RdmaRailEndpoint {
            node_id: "node-a".into(),
            advertised_endpoint: "10.0.0.1:50053".into(),
            fabric_id: "fabric-a".into(),
            listener_endpoint: "10.0.0.9:50054".into(),
        },
        pb::RdmaRailEndpoint {
            node_id: "node-b".into(),
            advertised_endpoint: "10.0.0.2:50053".into(),
            fabric_id: "fabric-a".into(),
            listener_endpoint: "10.0.0.9:50054".into(),
        },
    ];
    assert!(RailReader::discover_from_placement(
        &placement,
        &[LocalRailPath::new("rail0", "fabric-a", "mock0")],
        RailLimits::default(),
    )
    .is_err());
}

#[test]
fn fixed_task_cap_preserves_nine_routes_and_rejects_excess() {
    let make_routes = |count| {
        (0..count)
            .map(|index| {
                RailRoute::new(
                    format!("rail{index}"),
                    "10.0.0.1:50053",
                    crate::rdma::RdmaClientConfig::new(
                        format!("10.0.0.1:{}", 50053 + index),
                        format!("mock{index}"),
                    ),
                )
            })
            .collect()
    };
    assert!(RailReader::new(make_routes(9), RailLimits::default()).is_ok());
    assert!(RailReader::new(make_routes(33), RailLimits::default()).is_err());
}

#[test]
fn partial_rail_failure_leaves_caller_buffer_unchanged() {
    let mut mock = MockTransport::new(vec![0x42; 64]);
    mock.failing_rail = Some("rail1".into());
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    assert_eq!(destination, vec![0xA5; 64]);
    assert_eq!(mock.calls.lock().unwrap().len(), 2);
}

#[test]
fn duplicate_stripe_is_rejected_before_transport() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    placement.chunks[1].stripe_index = 0;
    let mut destination = vec![0xA5; 64];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    assert!(mock.calls.lock().unwrap().is_empty());
    assert_eq!(destination, vec![0xA5; 64]);
}

#[test]
fn registration_budget_rejects_before_transport() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let limits = RailLimits {
        max_registered_bytes: 16,
        ..RailLimits::default()
    };
    let reader = RailReader::new(routes(), limits).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    assert!(mock.calls.lock().unwrap().is_empty());
}

#[test]
fn process_memlock_headroom_caps_registered_bytes_before_dispatch() {
    let budget = effective_registered_budget(4 * 1024 * 1024 * 1024, Some(500_012 * 1024));
    assert_eq!(budget, 500_012 * 1024 - (500_012 * 1024) / 5);
    assert!(budget < 4 * 128 * 1024 * 1024);
    assert_eq!(effective_registered_budget(1024, None), 1024);
}

#[test]
fn per_rail_inflight_limit_counts_concurrent_requests() {
    let (descriptor, placement) = fixture(64, 8);
    let configured = vec![routes()[0].clone()];
    let plan = RailPlan::build(&descriptor, &placement, &configured).unwrap();
    let limits = RailLimits {
        max_active_reads: 2,
        max_inflight_bytes_per_rail: 96,
        ..RailLimits::default()
    };
    let reader = RailReader::new(configured, limits).unwrap();
    let first = reader.reserve(&plan).unwrap();
    assert!(matches!(
        reader.reserve(&plan),
        Err(RailReadError::ResourceExhausted(_))
    ));
    drop(first);
    assert!(reader.reserve(&plan).is_ok());
}

#[test]
fn per_rail_task_limit_counts_concurrent_requests() {
    let (descriptor, placement) = fixture(64, 8);
    let configured = vec![routes()[0].clone()];
    let plan = RailPlan::build(&descriptor, &placement, &configured).unwrap();
    let limits = RailLimits {
        max_active_reads: 2,
        max_active_reads_per_rail: 1,
        ..RailLimits::default()
    };
    let reader = RailReader::new(configured, limits).unwrap();
    let first = reader.reserve(&plan).unwrap();
    assert!(matches!(
        reader.reserve(&plan),
        Err(RailReadError::ResourceExhausted(_))
    ));
    drop(first);
    assert!(reader.reserve(&plan).is_ok());
}

#[test]
fn completed_payload_keeps_staging_and_active_read_reserved_until_drop() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let limits = RailLimits {
        max_active_reads: 1,
        ..RailLimits::default()
    };
    let reader = RailReader::new(routes(), limits).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let staged = reader
        .read_staged_with(&descriptor, &placement, &mock, None)
        .unwrap();
    assert_eq!(reader.budget.lock().unwrap().active_reads, 1);
    assert!(reader.budget.lock().unwrap().staging_bytes >= 64);
    assert!(reader
        .budget
        .lock()
        .unwrap()
        .rail_active_reads
        .iter()
        .all(|count| *count == 0));
    assert!(reader
        .budget
        .lock()
        .unwrap()
        .rail_inflight_bytes
        .iter()
        .all(|bytes| *bytes == 0));
    assert!(matches!(
        reader.read_staged_with(&descriptor, &placement, &mock, None),
        Err(RailReadError::ResourceExhausted(_))
    ));
    drop(staged);
    assert_eq!(reader.budget.lock().unwrap().active_reads, 0);
}

#[test]
fn cancellation_before_publish_preserves_caller_buffer() {
    let (descriptor, placement) = fixture(64, 8);
    let lookup = crate::ObjectLookup {
        descriptor,
        placement: Some(placement),
    };
    let cancel = RailCancel::default();
    cancel.cancel();
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        commit_if_unchanged(
            &lookup,
            &lookup,
            &[0x42; 64],
            &mut destination,
            Some(&cancel)
        ),
        Err(RailReadError::Cancelled)
    ));
    assert_eq!(destination, vec![0xA5; 64]);
}

#[test]
fn cancellation_and_publish_have_one_order() {
    use std::sync::mpsc;
    let token = RailCancel::default();
    let (publishing_tx, publishing_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let committing = token.clone();
    let publish = std::thread::spawn(move || {
        committing.publish_if_live(|| {
            publishing_tx.send(()).unwrap();
            release_rx.recv().unwrap();
        })
    });
    publishing_rx.recv().unwrap();
    let cancelling = token.clone();
    let cancel = std::thread::spawn(move || cancelling.cancel());
    assert!(!token.is_cancelled());
    release_tx.send(()).unwrap();
    publish.join().unwrap().unwrap();
    cancel.join().unwrap();
    assert!(token.is_cancelled());
}

#[test]
fn descriptor_identity_rejects_generation_and_layout_changes() {
    let (original, _) = fixture(64, 8);
    let mut changed = original.clone();
    assert!(same_descriptor_identity(&original, &changed));
    changed.object_generation += 1;
    assert!(!same_descriptor_identity(&original, &changed));
    changed = original.clone();
    changed.layout_version += 1;
    assert!(!same_descriptor_identity(&original, &changed));
}

#[test]
fn two_rails_for_one_node_require_distinct_local_ports() {
    let mut rails = routes();
    rails[1].connection.device = rails[0].connection.device.clone();
    assert!(RailReader::new(rails, RailLimits::default()).is_err());
}

#[test]
fn weighted_scheduler_assigns_more_stripes_to_faster_rail() {
    let mut configured = routes();
    configured[1] = configured[1].clone().with_weight(3);
    let (descriptor, placement) = fixture(80, 8);
    let plan = RailPlan::build(&descriptor, &placement, &configured).unwrap();
    assert_eq!(plan.tasks.len(), 2);
    assert!(plan.tasks[1].packed_len > plan.tasks[0].packed_len);
    assert_eq!(
        plan.tasks.iter().map(|task| task.packed_len).sum::<usize>(),
        80
    );
}

#[cfg(unix)]
#[test]
fn topology_reads_numa_and_pci_address_from_sysfs_shape() {
    let temp = tempfile::tempdir().unwrap();
    let device = temp.path().join("mlx5_0");
    let pci = temp.path().join("0000:03:00.0");
    std::fs::create_dir_all(&device).unwrap();
    std::fs::create_dir_all(&pci).unwrap();
    std::fs::write(pci.join("numa_node"), "1\n").unwrap();
    std::os::unix::fs::symlink(&pci, device.join("device")).unwrap();
    let topology = read_topology_from(temp.path(), "mlx5_0");
    assert_eq!(topology.numa_node, Some(1));
    assert_eq!(topology.pci_bdf.as_deref(), Some("0000:03:00.0"));
}

#[cfg(unix)]
#[test]
fn virtual_soft_roce_device_is_not_reported_as_pci_hardware() {
    let temp = tempfile::tempdir().unwrap();
    let device = temp.path().join("rxe0");
    let virtual_device = temp.path().join("virtual-rxe0");
    std::fs::create_dir_all(&device).unwrap();
    std::fs::create_dir_all(&virtual_device).unwrap();
    std::os::unix::fs::symlink(&virtual_device, device.join("device")).unwrap();
    let topology = read_topology_from(temp.path(), "rxe0");
    assert_eq!(topology.pci_bdf, None);
}

#[test]
fn one_rail_restores_the_same_object_layout() {
    let bytes: Vec<u8> = (0..64).map(|index| index as u8).collect();
    let mock = MockTransport::new(bytes.clone());
    let reader = RailReader::new(vec![routes()[0].clone()], RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert_eq!(
        reader
            .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            .unwrap(),
        64
    );
    assert_eq!(destination, bytes);
    assert_eq!(mock.calls.lock().unwrap().len(), 1);
}

#[test]
fn disabled_second_rail_falls_back_to_first_listener() {
    let bytes = vec![0x42; 64];
    let mock = MockTransport::new(bytes.clone());
    let mut configured = routes();
    configured[1] = configured[1].clone().disabled();
    let reader = RailReader::new(configured, RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    assert_eq!(destination, bytes);
    assert_eq!(mock.calls.lock().unwrap().as_slice(), ["10.0.0.1:50053"]);
}

#[test]
fn runtime_disable_and_enable_replans_without_changing_placement() {
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    assert!(reader.set_enabled("rail1", false));
    let mock = MockTransport::new(vec![0x42; 64]);
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    assert_eq!(mock.calls.lock().unwrap().as_slice(), ["10.0.0.1:50053"]);
    assert!(reader.set_enabled("rail1", true));
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    assert_eq!(mock.calls.lock().unwrap().len(), 3);
}

#[test]
fn failed_rail_cools_down_then_recovers_on_next_request() {
    let limits = RailLimits {
        rail_cooldown: Duration::from_millis(30),
        ..RailLimits::default()
    };
    let reader = RailReader::new(routes(), limits).unwrap();
    let mut failed = MockTransport::new(vec![0x42; 64]);
    failed.failing_rail = Some("rail1".into());
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &failed, None)
        .is_err());
    assert!(!reader.snapshots()[1].healthy);

    let recovered = MockTransport::new(vec![0x42; 64]);
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &recovered, None)
        .unwrap();
    assert_eq!(recovered.calls.lock().unwrap().len(), 1);
    std::thread::sleep(Duration::from_millis(40));
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &recovered, None)
        .unwrap();
    assert_eq!(recovered.calls.lock().unwrap().len(), 3);
}

#[test]
fn stale_descriptor_does_not_mark_healthy_transport_as_down() {
    struct StaleMock(MockTransport);
    impl RailTransport for StaleMock {
        fn fetch(
            &self,
            route: &RailRoute,
            task: &RailTask,
            descriptor: &pb::ObjectDescriptor,
            timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            if route.id == "rail1" {
                Err(RailReadError::StaleDescriptor)
            } else {
                self.0.fetch(route, task, descriptor, timeout)
            }
        }
    }
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    let transport = StaleMock(MockTransport::new(vec![0x42; 64]));
    assert!(matches!(
        reader.read_into_with(&descriptor, &placement, &mut destination, &transport, None),
        Err(RailReadError::StaleDescriptor)
    ));
    assert!(reader.snapshots().iter().all(|rail| rail.healthy));
}

#[test]
fn checksum_failure_keeps_destination_unchanged() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    for chunk in &mut placement.chunks {
        chunk.checksum = "0000000000000000".into();
    }
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
        Err(RailReadError::Checksum { stripe: 0 })
    ));
    assert_eq!(destination, vec![0xA5; 64]);
}

#[test]
fn matching_stripe_checksums_allow_complete_read() {
    let payload: Vec<u8> = (0..64).map(|index| index as u8).collect();
    let mock = MockTransport::new(payload.clone());
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    for chunk in &mut placement.chunks {
        let start = chunk.offset as usize;
        let end = start + chunk.length as usize;
        chunk.checksum = format!("{:016x}", twox_hash::xxh3::hash64(&payload[start..end]));
    }
    let mut destination = vec![0xA5; 64];
    assert_eq!(
        reader
            .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            .unwrap(),
        64
    );
    assert_eq!(destination, payload);
}

#[test]
fn missing_or_out_of_bounds_stripe_is_rejected_before_dispatch() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    placement.chunks.pop();
    let mut destination = vec![0xA5; 64];
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    let (_, mut placement) = fixture(64, 8);
    placement.chunks[1].offset = 99;
    assert!(reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .is_err());
    assert!(mock.calls.lock().unwrap().is_empty());
}

#[test]
fn placement_for_another_object_is_rejected_before_dispatch() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    placement.key.as_mut().unwrap().object_key = "different".into();
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
        Err(RailReadError::InvalidPlacement(_))
    ));
    assert!(mock.calls.lock().unwrap().is_empty());
}

#[test]
fn partially_populated_checksums_are_rejected_before_dispatch() {
    let mock = MockTransport::new(vec![0x42; 64]);
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, mut placement) = fixture(64, 8);
    placement.chunks[0].checksum = "0000000000000000".into();
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
        Err(RailReadError::InvalidPlacement(_))
    ));
    assert!(mock.calls.lock().unwrap().is_empty());
}

#[test]
fn cancellation_waits_for_started_mock_transfer_and_preserves_buffer() {
    use std::sync::mpsc;
    struct SlowMock {
        base: MockTransport,
        started: Mutex<Option<mpsc::Sender<()>>>,
    }
    impl RailTransport for SlowMock {
        fn fetch(
            &self,
            route: &RailRoute,
            task: &RailTask,
            descriptor: &pb::ObjectDescriptor,
            timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            if let Some(sender) = self.started.lock().unwrap().take() {
                let _ = sender.send(());
            }
            std::thread::sleep(Duration::from_millis(60));
            self.base.fetch(route, task, descriptor, timeout)
        }
    }
    let (started_tx, started_rx) = mpsc::channel();
    let mock = SlowMock {
        base: MockTransport::new(vec![0x42; 64]),
        started: Mutex::new(Some(started_tx)),
    };
    let token = RailCancel::default();
    let cancelling = token.clone();
    let canceller = std::thread::spawn(move || {
        started_rx.recv().unwrap();
        cancelling.cancel();
    });
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    let started = Instant::now();
    assert!(matches!(
        reader.read_into_with(
            &descriptor,
            &placement,
            &mut destination,
            &mock,
            Some(&token)
        ),
        Err(RailReadError::Cancelled)
    ));
    canceller.join().unwrap();
    assert!(started.elapsed() >= Duration::from_millis(60));
    assert_eq!(destination, vec![0xA5; 64]);
    destination.fill(0x33);
    std::thread::sleep(Duration::from_millis(20));
    assert_eq!(destination, vec![0x33; 64]);
}

#[test]
fn sge_map_keeps_every_stripe_inside_its_registered_region() {
    let (descriptor, placement) = fixture(64, 8);
    let plan = RailPlan::build(&descriptor, &placement, &routes()).unwrap();
    let task = &plan.tasks[0];
    let base = 0x1000u64;
    let segments = build_sge_segments(task, &descriptor, base, 7).unwrap();
    assert_eq!(segments.len(), 8);
    assert_eq!(segments.iter().map(|(_, _, len)| len).sum::<u64>(), 64);
    for (address, rkey, length) in &segments {
        assert_eq!(*rkey, 7);
        assert!(*address >= base);
        assert!(*address + *length <= base + (task.packed_len + task.dummy_len) as u64);
    }
    assert_eq!(segments[0].0, base);
    assert_eq!(segments[1].0, base + task.packed_len as u64);
    assert_eq!(segments[2].0, base + 8);
}

#[test]
fn changed_generation_cannot_publish_completed_rail_bytes() {
    let (descriptor, placement) = fixture(64, 8);
    let initial = crate::ObjectLookup {
        descriptor: descriptor.clone(),
        placement: Some(placement.clone()),
    };
    let mut current = initial.clone();
    current.descriptor.object_generation += 1;
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        commit_if_unchanged(&initial, &current, &[0x42; 64], &mut destination, None),
        Err(RailReadError::VersionChanged)
    ));
    assert_eq!(destination, vec![0xA5; 64]);
    current = initial.clone();
    current.placement.as_mut().unwrap().layout_hash = "moved".into();
    assert!(matches!(
        commit_if_unchanged(&initial, &current, &[0x42; 64], &mut destination, None),
        Err(RailReadError::VersionChanged)
    ));
    assert_eq!(destination, vec![0xA5; 64]);
    current = initial.clone();
    assert_eq!(
        commit_if_unchanged(&initial, &current, &[0x42; 64], &mut destination, None).unwrap(),
        64
    );
    assert_eq!(destination, vec![0x42; 64]);
}

#[test]
fn panicked_worker_releases_inflight_metrics_and_preserves_buffer() {
    struct PanicTransport;
    impl RailTransport for PanicTransport {
        fn fetch(
            &self,
            _route: &RailRoute,
            _task: &RailTask,
            _descriptor: &pb::ObjectDescriptor,
            _timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            panic!("injected worker panic")
        }
    }
    let reader = RailReader::new(vec![routes()[0].clone()], RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(64, 8);
    let mut destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(
            &descriptor,
            &placement,
            &mut destination,
            &PanicTransport,
            None
        ),
        Err(RailReadError::WorkerPanic)
    ));
    assert_eq!(reader.snapshots()[0].inflight_bytes, 0);
    assert_eq!(destination, vec![0xA5; 64]);
}

#[test]
fn active_read_limit_rejects_concurrent_second_request() {
    use std::sync::mpsc;
    struct HeldTransport {
        object: MockTransport,
        started: Mutex<Option<mpsc::Sender<()>>>,
        release: Mutex<mpsc::Receiver<()>>,
    }
    impl RailTransport for HeldTransport {
        fn fetch(
            &self,
            route: &RailRoute,
            task: &RailTask,
            descriptor: &pb::ObjectDescriptor,
            timeout: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            if let Some(sender) = self.started.lock().unwrap().take() {
                let _ = sender.send(());
            }
            self.release.lock().unwrap().recv().unwrap();
            self.object.fetch(route, task, descriptor, timeout)
        }
    }
    let limits = RailLimits {
        max_active_reads: 1,
        ..RailLimits::default()
    };
    let reader = Arc::new(RailReader::new(routes(), limits).unwrap());
    let (descriptor, placement) = fixture(64, 8);
    let (started_tx, started_rx) = mpsc::channel();
    let (release_tx, release_rx) = mpsc::channel();
    let held = HeldTransport {
        object: MockTransport::new(vec![0x42; 64]),
        started: Mutex::new(Some(started_tx)),
        release: Mutex::new(release_rx),
    };
    let first_reader = Arc::clone(&reader);
    let first_desc = descriptor.clone();
    let first_placement = placement.clone();
    let first = std::thread::spawn(move || {
        let mut destination = vec![0xA5; 64];
        first_reader
            .read_into_with(&first_desc, &first_placement, &mut destination, &held, None)
            .map(|_| destination)
    });
    started_rx.recv().unwrap();
    let second_mock = MockTransport::new(vec![0x42; 64]);
    let mut second_destination = vec![0xA5; 64];
    assert!(matches!(
        reader.read_into_with(
            &descriptor,
            &placement,
            &mut second_destination,
            &second_mock,
            None
        ),
        Err(RailReadError::ResourceExhausted(_))
    ));
    assert!(second_mock.calls.lock().unwrap().is_empty());
    release_tx.send(()).unwrap();
    release_tx.send(()).unwrap();
    assert_eq!(first.join().unwrap().unwrap(), vec![0x42; 64]);
    assert_eq!(second_destination, vec![0xA5; 64]);
}

#[test]
#[ignore = "software-only Mock measurement; this is not RDMA bandwidth"]
fn software_only_mock_benchmark() {
    fn process_usage() -> (u64, u64, u64) {
        let mut usage = unsafe { std::mem::zeroed::<libc::rusage>() };
        assert_eq!(unsafe { libc::getrusage(libc::RUSAGE_SELF, &mut usage) }, 0);
        let micros = |time: libc::timeval| time.tv_sec as u64 * 1_000_000 + time.tv_usec as u64;
        (
            micros(usage.ru_utime),
            micros(usage.ru_stime),
            usage.ru_maxrss as u64,
        )
    }
    let env_number = |name: &str, default: usize| {
        std::env::var(name)
            .ok()
            .and_then(|value| value.parse().ok())
            .unwrap_or(default)
    };
    let mib = env_number("CS_RAIL_MOCK_MIB", 64);
    let iterations = env_number("CS_RAIL_MOCK_ITERS", 5);
    let rail_count = env_number("CS_RAIL_MOCK_RAILS", 2);
    assert!(mib > 0 && iterations > 0 && (1..=2).contains(&rail_count));
    let size = mib * 1024 * 1024;
    let object = vec![0x42; size];
    let mock = MockTransport::new(object);
    let reader = RailReader::new(routes()[..rail_count].to_vec(), RailLimits::default()).unwrap();
    let (descriptor, placement) = fixture(size, 4 * 1024 * 1024);
    let mut destination = vec![0xA5; size];
    reader
        .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
        .unwrap();
    let warmup_bytes: Vec<_> = reader
        .snapshots()
        .iter()
        .map(|snapshot| snapshot.bytes)
        .collect();
    let mut durations = Vec::with_capacity(iterations);
    let mut cpu_user_us = 0u64;
    let mut cpu_system_us = 0u64;
    for _ in 0..iterations {
        destination.fill(0xA5);
        let before_cpu = process_usage();
        let start = Instant::now();
        reader
            .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            .unwrap();
        durations.push(start.elapsed().as_micros() as u64);
        let after_cpu = process_usage();
        cpu_user_us += after_cpu.0 - before_cpu.0;
        cpu_system_us += after_cpu.1 - before_cpu.1;
        assert!(destination.iter().all(|byte| *byte == 0x42));
    }
    let average = durations.iter().sum::<u64>() / iterations as u64;
    println!("mock_samples_us={durations:?}");
    let gib_per_s = size as f64 * 1_000_000.0 / average as f64 / 1024f64.powi(3);
    let bytes: Vec<_> = reader
        .snapshots()
        .iter()
        .zip(warmup_bytes)
        .map(|(snapshot, warmup)| snapshot.bytes - warmup)
        .collect();
    println!(
            "mock_only,rails={rail_count},size_bytes={size},iters={iterations},avg_us={average},gib_per_s={gib_per_s:.3},cpu_user_us={cpu_user_us},cpu_system_us={cpu_system_us},peak_rss_kb={},rail_bytes={bytes:?}",
            process_usage().2
        );
}

#[test]
fn connection_pool_is_opt_in_and_rejects_zero_capacity() {
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    assert!(reader.connection_pool_snapshot().is_none());
    assert!(reader
        .with_connection_pool(RailConnectionPoolOptions {
            max_live_connections: 0,
            configuration_generation: 1,
        })
        .is_err());
}

#[test]
fn connection_keys_separate_route_owner_device_listener_and_generation() {
    let route = routes().remove(0);
    let key = ConnectionKey::new(&route, "node-a", 7);
    let pool = ConnectionPool::new(1);
    pool.acquire(key.clone(), || Ok(0))
        .unwrap()
        .release_healthy();
    let mut variants = Vec::new();
    let mut changed = route.clone();
    changed.id = "other-route".into();
    variants.push(ConnectionKey::new(&changed, "node-a", 7));
    let mut changed = route.clone();
    changed.advertised_endpoint = "other-owner:1".into();
    variants.push(ConnectionKey::new(&changed, "node-a", 7));
    let mut changed = route.clone();
    changed.connection.endpoint = "other-listener:1".into();
    variants.push(ConnectionKey::new(&changed, "node-a", 7));
    let mut changed = route.clone();
    changed.connection.device = "other-device".into();
    variants.push(ConnectionKey::new(&changed, "node-a", 7));
    let mut changed = route.clone();
    changed.connection.port += 1;
    variants.push(ConnectionKey::new(&changed, "node-a", 7));
    let mut changed = route.clone();
    changed.connection.gid_index += 1;
    variants.push(ConnectionKey::new(&changed, "node-a", 7));
    variants.push(ConnectionKey::new(&route, "node-b", 7));
    variants.push(ConnectionKey::new(&route, "node-a", 8));
    for (index, variant) in variants.into_iter().enumerate() {
        assert_ne!(variant, key);
        let mut lease = pool.acquire(variant, || Ok(index + 1)).unwrap();
        assert_eq!(*lease.client(), index + 1);
        lease.release_healthy();
    }
    assert_eq!(pool.snapshot().created, 9);
    assert_eq!(pool.snapshot().reused, 0);
    assert_eq!(pool.snapshot().retired, 8);
}

#[test]
fn pooled_transport_rejects_ambiguous_placement_owner_before_opening_connections() {
    let (descriptor, mut placement) = fixture(16, 4);
    placement.chunks[1].node_id = "different-owner".into();
    let reader = RailReader::new(routes(), RailLimits::default()).unwrap();
    assert!(VerbsTransport::new(&reader, &placement, &descriptor).is_ok());
    let reader = reader
        .with_connection_pool(RailConnectionPoolOptions {
            max_live_connections: 2,
            configuration_generation: 1,
        })
        .unwrap();
    assert!(matches!(
        VerbsTransport::new(&reader, &placement, &descriptor),
        Err(RailReadError::InvalidPlacement(_))
    ));
    assert_eq!(reader.connection_pool_snapshot().unwrap().created, 0);
}

#[test]
fn incomplete_ack_never_returns_connection_to_pool() {
    let (descriptor, placement) = fixture(16, 4);
    let plan = RailPlan::build(&descriptor, &placement, &routes()).unwrap();
    let task = &plan.tasks[0];
    for outcome in [
        Some(RdmaReadOutcome {
            bytes: task.packed_len - 1,
            chunks: task.stripes.len() as u32,
        }),
        Some(RdmaReadOutcome {
            bytes: task.packed_len,
            chunks: task.stripes.len() as u32 + 1,
        }),
        None,
    ] {
        let pool = ConnectionPool::new(1);
        let lease = pool.acquire("route", || Ok(1)).unwrap();
        let result = validate_outcome(Ok(outcome), task, &descriptor);
        assert!(finish_transfer(lease, (), Ok(result)).is_err());
        assert_eq!(pool.snapshot().idle, 0);
        assert_eq!(pool.snapshot().retired, 1);
        assert_eq!(pool.snapshot().live, 0);
    }
}

#[test]
fn unstriped_ack_accepts_multiple_server_writes_for_the_complete_object() {
    let (mut descriptor, placement) = fixture(16, 16);
    descriptor.is_striped = false;
    let plan = RailPlan::build(&descriptor, &placement, &routes()).unwrap();
    let task = &plan.tasks[0];
    assert_eq!(task.stripes.len(), 1);
    for writes in [2, 7] {
        assert!(
            validate_outcome(
                Ok(Some(RdmaReadOutcome {
                    bytes: 16,
                    chunks: writes
                })),
                task,
                &descriptor,
            )
            .is_ok(),
            "complete-object ACK counts server WRITEs, not planner stripes"
        );
    }
}

#[test]
fn striped_ack_still_requires_exact_selected_stripe_count() {
    let (descriptor, placement) = fixture(16, 16);
    let plan = RailPlan::build(&descriptor, &placement, &routes()).unwrap();
    let task = &plan.tasks[0];
    assert!(validate_outcome(
        Ok(Some(RdmaReadOutcome {
            bytes: 16,
            chunks: 1
        })),
        task,
        &descriptor,
    )
    .is_ok());
    for chunks in [0, 2] {
        assert!(matches!(
            validate_outcome(
                Ok(Some(RdmaReadOutcome { bytes: 16, chunks })),
                task,
                &descriptor,
            ),
            Err(RailReadError::Incomplete { .. })
        ));
    }
}

#[test]
fn unstriped_reads_bypass_pooled_owner_constraints_and_keep_legacy_byte_validation() {
    let (mut descriptor, mut placement) = fixture(16, 16);
    descriptor.is_striped = false;
    placement.chunks[0].node_id.clear();
    let reader = RailReader::new(routes(), RailLimits::default())
        .unwrap()
        .with_connection_pool(RailConnectionPoolOptions {
            max_live_connections: 1,
            configuration_generation: 1,
        })
        .unwrap();
    let transport = VerbsTransport::new(&reader, &placement, &descriptor).unwrap();
    assert!(transport.pool.is_none());
    let plan = RailPlan::build(&descriptor, &placement, reader.routes()).unwrap();
    let task = &plan.tasks[0];
    // A nonstriped reply's diagnostic count was never a legacy success gate.
    assert!(validate_outcome(
        Ok(Some(RdmaReadOutcome {
            bytes: 16,
            chunks: 0
        })),
        task,
        &descriptor
    )
    .is_ok());
    assert!(matches!(
        validate_outcome(
            Ok(Some(RdmaReadOutcome {
                bytes: 15,
                chunks: 7
            })),
            task,
            &descriptor
        ),
        Err(RailReadError::Incomplete { .. })
    ));
    assert_eq!(
        reader.connection_pool_snapshot().unwrap(),
        Default::default()
    );
}

fn assert_recovery_budget_empty(reader: &RailReader) {
    let budget = reader.budget.lock().unwrap();
    assert_eq!(budget.active_reads, 0);
    assert_eq!(budget.staging_bytes, 0);
    assert_eq!(budget.registered_bytes, 0);
    assert_eq!(budget.inflight_bytes, 0);
    assert!(budget.rail_active_reads.iter().all(|count| *count == 0));
    assert!(budget.rail_inflight_bytes.iter().all(|bytes| *bytes == 0));
    assert!(reader
        .snapshots()
        .iter()
        .all(|rail| rail.inflight_requests == 0
            && rail.inflight_bytes == 0
            && rail.registered_bytes == 0));
}

struct RecoveryScript<F>(F);
impl<F> RailTransport for RecoveryScript<F>
where
    F: Fn(&RailRoute, &RailTask, &pb::ObjectDescriptor, Duration) -> Result<Vec<u8>, RailReadError>
        + Sync,
{
    fn fetch(
        &self,
        route: &RailRoute,
        task: &RailTask,
        descriptor: &pb::ObjectDescriptor,
        timeout: Duration,
    ) -> Result<Vec<u8>, RailReadError> {
        (self.0)(route, task, descriptor, timeout)
    }
}

fn recovery_reader(direct_staging: bool) -> RailReader {
    RailReader::new(
        routes(),
        RailLimits {
            direct_staging,
            ..RailLimits::default()
        },
    )
    .unwrap()
    .with_recovery(RailRecoveryOptions {
        timeout: Duration::from_secs(5),
    })
    .unwrap()
}

#[test]
fn recovery_waits_for_every_initial_worker_and_cancel_stops_the_wave() {
    use std::sync::mpsc;
    for direct in [false, true] {
        for cancel_before_release in [false, true] {
            let reader = recovery_reader(direct);
            let cancel = RailCancel::default();
            let (started_tx, started_rx) = mpsc::channel();
            let (release_tx, release_rx) = mpsc::channel();
            let release_rx = Mutex::new(release_rx);
            let calls = Mutex::new(Vec::new());
            let mock = RecoveryScript(
                |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, _: Duration| {
                    let initial = {
                        let mut calls = calls.lock().unwrap();
                        let initial = !calls
                            .iter()
                            .any(|(id, _): &(String, usize)| id == &route.id);
                        calls.push((route.id.clone(), task.packed_len));
                        initial
                    };
                    if initial {
                        started_tx.send(()).unwrap();
                        if route.id == "rail0" {
                            return Err(connection_pool::mock_quiescent_failure());
                        }
                        release_rx
                            .lock()
                            .unwrap()
                            .recv_timeout(Duration::from_secs(5))
                            .unwrap();
                    }
                    Ok(vec![0x42; task.packed_len])
                },
            );
            let (descriptor, placement) = fixture(64, 8);
            let mut destination = vec![0xA5; 64];
            std::thread::scope(|scope| {
                let worker = scope.spawn(|| {
                    reader.read_into_with(
                        &descriptor,
                        &placement,
                        &mut destination,
                        &mock,
                        Some(&cancel),
                    )
                });
                started_rx.recv_timeout(Duration::from_secs(5)).unwrap();
                started_rx.recv_timeout(Duration::from_secs(5)).unwrap();
                assert_eq!(
                    calls.lock().unwrap().len(),
                    2,
                    "retry cannot precede the join barrier"
                );
                {
                    let budget = reader.budget.lock().unwrap();
                    assert_eq!(budget.inflight_bytes, 64);
                    assert_eq!(budget.registered_bytes, 80);
                    assert_eq!(budget.rail_active_reads, vec![1, 1]);
                    assert_eq!(budget.staging_bytes, if direct { 80 } else { 144 });
                }
                if cancel_before_release {
                    cancel.cancel();
                }
                release_tx.send(()).unwrap();
                let result = worker.join().unwrap();
                if cancel_before_release {
                    assert!(matches!(result, Err(RailReadError::Cancelled)));
                } else {
                    assert_eq!(result.unwrap(), 64);
                }
            });
            assert_eq!(
                destination,
                vec![if cancel_before_release { 0xA5 } else { 0x42 }; 64]
            );
            assert_eq!(
                calls.lock().unwrap().len(),
                if cancel_before_release { 2 } else { 3 }
            );
            assert_recovery_budget_empty(&reader);
        }
    }
}

#[test]
fn recovery_rejects_uncertified_protocol_stale_incomplete_resource_and_panic_failures() {
    for direct in [false, true] {
        for failure in 0..7 {
            let reader = recovery_reader(direct);
            let calls = AtomicU64::new(0);
            let mock = RecoveryScript(
                |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, _: Duration| {
                    calls.fetch_add(1, Ordering::SeqCst);
                    if route.id != "rail0" {
                        return Ok(vec![0x42; task.packed_len]);
                    }
                    match failure {
                        0 => Err(RailReadError::Transport(
                            "ConnectionReset in untrusted string".into(),
                        )),
                        1 => Err(RailReadError::StaleDescriptor),
                        2 => Err(RailReadError::Incomplete {
                            expected: task.packed_len,
                            actual: 1,
                        }),
                        3 => Err(RailReadError::ResourceExhausted("MR failure".into())),
                        4 => Err(RailReadError::InvalidPlacement("bad owner".into())),
                        5 => Err(RailReadError::Cancelled),
                        _ => panic!("mock worker panic"),
                    }
                },
            );
            let (descriptor, placement) = fixture(64, 8);
            let mut destination = vec![0xA5; 64];
            assert!(reader
                .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
                .is_err());
            assert_eq!(destination, vec![0xA5; 64]);
            assert_eq!(calls.load(Ordering::SeqCst), 2);
            assert_recovery_budget_empty(&reader);
        }
    }
}

#[test]
fn recovery_checks_successful_task_checksums_before_retry() {
    for direct in [false, true] {
        let reader = recovery_reader(direct);
        let mock = RecoveringMock {
            object: vec![0x42; 64],
            calls: Mutex::new(Vec::new()),
        };
        let (descriptor, mut placement) = fixture(64, 8);
        for chunk in &mut placement.chunks {
            chunk.checksum = format!("{:016x}", twox_hash::xxh3::hash64(&[0x43; 8]));
        }
        let mut destination = vec![0xA5; 64];
        assert!(matches!(
            reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
            Err(RailReadError::Checksum { .. })
        ));
        assert_eq!(mock.calls.lock().unwrap().len(), 2);
        assert_eq!(destination, vec![0xA5; 64]);
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_requires_an_enabled_healthy_unambiguous_same_owner_alternate() {
    for case in 0..5 {
        let reader = recovery_reader(false);
        let (descriptor, mut placement) = fixture(64, 8);
        if case == 0 {
            reader.set_enabled("rail1", false);
        }
        if case == 1 {
            placement.chunks[0].node_id.clear();
        }
        if case == 2 {
            placement.chunks[0].node_id = "another-owner".into();
        }
        let calls = AtomicU64::new(0);
        let mock = RecoveryScript(
            |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, _: Duration| {
                calls.fetch_add(1, Ordering::SeqCst);
                if route.id == "rail0" || case == 3 {
                    if case == 4 {
                        reader.set_enabled("rail1", false);
                    }
                    Err(connection_pool::mock_quiescent_failure())
                } else {
                    Ok(vec![0x42; task.packed_len])
                }
            },
        );
        let mut destination = vec![0xA5; 64];
        assert!(matches!(
            reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None),
            Err(RailReadError::QuiescentTransport(_))
        ));
        assert_eq!(calls.load(Ordering::SeqCst), if case == 0 { 1 } else { 2 });
        assert_eq!(destination, vec![0xA5; 64]);
        assert_recovery_budget_empty(&reader);
    }
    // A path to another advertised owner can never serve as a replica.
    let mut configured = routes();
    configured[1].advertised_endpoint = "10.9.9.9:50053".into();
    let reader = RailReader::new(configured, RailLimits::default())
        .unwrap()
        .with_recovery(RailRecoveryOptions {
            timeout: Duration::from_secs(5),
        })
        .unwrap();
    let mock = RecoveringMock {
        object: vec![0x42; 64],
        calls: Mutex::new(Vec::new()),
    };
    let (descriptor, placement) = fixture(64, 8);
    assert!(reader
        .read_staged_with(&descriptor, &placement, &mock, None)
        .is_err());
    assert_eq!(mock.calls.lock().unwrap().len(), 1);
    assert_recovery_budget_empty(&reader);
}

#[test]
fn recovery_never_retries_unstriped_or_default_reader_failures() {
    for unstriped in [false, true] {
        let reader = if unstriped {
            recovery_reader(false)
        } else {
            RailReader::new(routes(), RailLimits::default()).unwrap()
        };
        let (mut descriptor, placement) = fixture(64, if unstriped { 64 } else { 8 });
        descriptor.is_striped = !unstriped;
        let mock = RecoveringMock {
            object: vec![0x42; 64],
            calls: Mutex::new(Vec::new()),
        };
        assert!(reader
            .read_staged_with(&descriptor, &placement, &mock, None)
            .is_err());
        assert_eq!(
            mock.calls.lock().unwrap().len(),
            if unstriped { 1 } else { 2 }
        );
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_two_failed_tasks_share_one_alternate_sequentially() {
    for direct in [false, true] {
        let mut configured = routes();
        configured.push(RailRoute::new(
            "rail2",
            "10.0.0.1:50053",
            crate::rdma::RdmaClientConfig::new("10.0.2.1:50055", "mock2"),
        ));
        let reader = RailReader::new(
            configured,
            RailLimits {
                direct_staging: direct,
                max_active_reads_per_rail: 1,
                max_inflight_bytes_per_rail: 24,
                rail_cooldown: Duration::ZERO,
                ..RailLimits::default()
            },
        )
        .unwrap()
        .with_recovery(RailRecoveryOptions {
            timeout: Duration::from_secs(5),
        })
        .unwrap();
        let calls = Mutex::new(Vec::new());
        let mock = RecoveryScript(
            |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, _: Duration| {
                calls.lock().unwrap().push((
                    route.id.clone(),
                    task.stripes
                        .iter()
                        .map(|stripe| stripe.index)
                        .collect::<Vec<_>>(),
                ));
                if route.id != "rail2" {
                    return Err(connection_pool::mock_quiescent_failure());
                }
                let budget = reader.budget.lock().unwrap();
                assert_eq!(budget.rail_active_reads[2], 1);
                assert!(budget.rail_inflight_bytes[2] <= 24);
                assert_eq!(
                    budget.inflight_bytes, 72,
                    "global allocation stays charged during recovery"
                );
                Ok(vec![0x42; task.packed_len])
            },
        );
        let (descriptor, placement) = fixture(72, 8);
        let mut destination = vec![0xA5; 72];
        reader
            .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            .unwrap();
        assert_eq!(destination, vec![0x42; 72]);
        let calls = calls.lock().unwrap();
        assert_eq!(calls.len(), 5);
        assert_eq!(calls.iter().filter(|(rail, _)| rail == "rail2").count(), 3);
        assert!(calls[3..].iter().all(|(rail, _)| rail == "rail2"));
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_does_not_retry_a_failed_retry() {
    for direct in [false, true] {
        let reader = recovery_reader(direct);
        let calls = Mutex::new(Vec::new());
        let mock = RecoveryScript(
            |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, _: Duration| {
                calls.lock().unwrap().push(route.id.clone());
                if task.stripes[0].index == 0 {
                    return Err(connection_pool::mock_quiescent_failure());
                }
                Ok(vec![0x42; task.packed_len])
            },
        );
        let (descriptor, placement) = fixture(64, 8);
        let mut destination = vec![0xA5; 64];
        assert!(reader
            .read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            .is_err());
        assert_eq!(calls.lock().unwrap().len(), 3);
        assert_eq!(destination, vec![0xA5; 64]);
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_obeys_other_reads_active_and_byte_limits_on_the_alternate() {
    use std::sync::mpsc;
    for byte_limit in [false, true] {
        let reader = RailReader::new(
            routes(),
            RailLimits {
                max_active_reads_per_rail: if byte_limit { 2 } else { 1 },
                max_inflight_bytes_per_rail: if byte_limit { 64 } else { 128 },
                ..RailLimits::default()
            },
        )
        .unwrap()
        .with_recovery(RailRecoveryOptions {
            timeout: Duration::from_secs(5),
        })
        .unwrap();
        reader.set_enabled("rail1", false);
        let (entered_tx, entered_rx) = mpsc::channel();
        let (fail_tx, fail_rx) = mpsc::channel();
        let (other_tx, other_rx) = mpsc::channel();
        let fail_rx = Mutex::new(fail_rx);
        let other_rx = Mutex::new(other_rx);
        let calls = AtomicU64::new(0);
        let mock = RecoveryScript(
            |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, _: Duration| {
                calls.fetch_add(1, Ordering::SeqCst);
                entered_tx.send(()).unwrap();
                if route.id == "rail0" {
                    fail_rx
                        .lock()
                        .unwrap()
                        .recv_timeout(Duration::from_secs(5))
                        .unwrap();
                    Err(connection_pool::mock_quiescent_failure())
                } else {
                    other_rx
                        .lock()
                        .unwrap()
                        .recv_timeout(Duration::from_secs(5))
                        .unwrap();
                    Ok(vec![0x42; task.packed_len])
                }
            },
        );
        let (descriptor, placement) = fixture(64, 8);
        let mut destination = vec![0xA5; 64];
        std::thread::scope(|scope| {
            let failed = scope.spawn(|| {
                reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None)
            });
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            reader.set_enabled("rail0", false);
            reader.set_enabled("rail1", true);
            let other =
                scope.spawn(|| reader.read_staged_with(&descriptor, &placement, &mock, None));
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            fail_tx.send(()).unwrap();
            assert!(matches!(
                failed.join().unwrap(),
                Err(RailReadError::ResourceExhausted(_))
            ));
            assert_eq!(
                calls.load(Ordering::SeqCst),
                2,
                "rejected retries never reach transport"
            );
            assert_eq!(reader.budget.lock().unwrap().rail_active_reads, vec![0, 1]);
            other_tx.send(()).unwrap();
            drop(other.join().unwrap().unwrap());
        });
        assert_eq!(destination, vec![0xA5; 64]);
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_deadline_expires_at_barrier_without_resetting_or_retrying() {
    use std::sync::mpsc;
    for direct in [false, true] {
        let reader = recovery_reader(direct);
        let deadline = Instant::now() + Duration::from_millis(30);
        let (entered_tx, entered_rx) = mpsc::channel();
        let (release_tx, release_rx) = mpsc::channel();
        let release_rx = Mutex::new(release_rx);
        let calls = AtomicU64::new(0);
        let mock = RecoveryScript(
            |route: &RailRoute, task: &RailTask, _: &pb::ObjectDescriptor, timeout: Duration| {
                calls.fetch_add(1, Ordering::SeqCst);
                assert!(timeout <= Duration::from_millis(30));
                entered_tx.send(()).unwrap();
                if route.id == "rail0" {
                    return Err(connection_pool::mock_quiescent_failure());
                }
                release_rx
                    .lock()
                    .unwrap()
                    .recv_timeout(Duration::from_secs(5))
                    .unwrap();
                Ok(vec![0x42; task.packed_len])
            },
        );
        let (descriptor, placement) = fixture(64, 8);
        std::thread::scope(|scope| {
            let worker = scope.spawn(|| {
                reader.read_staged_with_deadline(
                    &descriptor,
                    &placement,
                    &mock,
                    None,
                    Some(deadline),
                )
            });
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
            // Latches control ownership; waiting for this fixed clock instant
            // tests the real monotonic deadline rather than a reset timeout.
            while Instant::now() < deadline {
                std::thread::park_timeout(deadline.saturating_duration_since(Instant::now()));
            }
            release_tx.send(()).unwrap();
            assert!(matches!(
                worker.join().unwrap(),
                Err(RailReadError::DeadlineExceeded)
            ));
        });
        assert_eq!(calls.load(Ordering::SeqCst), 2);
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_rechecks_version_capabilities_and_deadline_before_publication() {
    for direct in [false, true] {
        let reader = recovery_reader(direct);
        let (descriptor, placement) = fixture(64, 8);
        let initial = crate::ObjectLookup {
            descriptor: descriptor.clone(),
            placement: Some(placement.clone()),
        };
        let mock = RecoveringMock {
            object: vec![0x42; 64],
            calls: Mutex::new(Vec::new()),
        };
        let staged = reader
            .read_staged_with(&descriptor, &placement, &mock, None)
            .unwrap();
        for changed_capability in [false, true] {
            let mut current = initial.clone();
            if changed_capability {
                current
                    .placement
                    .as_mut()
                    .unwrap()
                    .rdma_rails
                    .push(pb::RdmaRailEndpoint::default());
            } else {
                current.descriptor.object_generation += 1;
            }
            let mut destination = vec![0xA5; 64];
            assert!(matches!(
                commit_if_unchanged_until(
                    &initial,
                    &current,
                    staged.as_bytes(),
                    &mut destination,
                    None,
                    staged.deadline
                ),
                Err(RailReadError::VersionChanged)
            ));
            assert_eq!(destination, vec![0xA5; 64]);
        }
        let mut destination = vec![0xA5; 64];
        let cancel = RailCancel::default();
        assert!(matches!(
            commit_if_unchanged_until(
                &initial,
                &initial,
                staged.as_bytes(),
                &mut destination,
                Some(&cancel),
                Some(Instant::now())
            ),
            Err(RailReadError::DeadlineExceeded)
        ));
        assert_eq!(destination, vec![0xA5; 64]);
        cancel.cancel();
        assert!(matches!(
            commit_if_unchanged_until(
                &initial,
                &initial,
                staged.as_bytes(),
                &mut destination,
                Some(&cancel),
                staged.deadline
            ),
            Err(RailReadError::Cancelled)
        ));
        assert_eq!(destination, vec![0xA5; 64]);
        drop(staged);
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_direct_partial_bytes_are_discarded_until_whole_task_succeeds() {
    struct PartialDirect {
        calls: Mutex<Vec<(String, Vec<u32>)>>,
        corrupt_retry: bool,
    }
    impl RailTransport for PartialDirect {
        fn fetch(
            &self,
            _: &RailRoute,
            _: &RailTask,
            _: &pb::ObjectDescriptor,
            _: Duration,
        ) -> Result<Vec<u8>, RailReadError> {
            panic!("must use disjoint direct ranges");
        }
        fn fetch_into(
            &self,
            route: &RailRoute,
            task: &RailTask,
            _: &pb::ObjectDescriptor,
            target: &mut [u8],
            _: Duration,
        ) -> Result<(), RailReadError> {
            self.calls.lock().unwrap().push((
                route.id.clone(),
                task.stripes.iter().map(|stripe| stripe.index).collect(),
            ));
            if route.id == "rail0" {
                target.fill(0xEE);
                return Err(connection_pool::mock_quiescent_failure());
            }
            target.fill(if self.corrupt_retry && task.stripes[0].index == 0 {
                0xDD
            } else {
                0x42
            });
            Ok(())
        }
    }
    for corrupt_retry in [false, true] {
        let reader = recovery_reader(true);
        let mock = PartialDirect {
            calls: Mutex::new(Vec::new()),
            corrupt_retry,
        };
        let (descriptor, mut placement) = fixture(64, 8);
        for chunk in &mut placement.chunks {
            chunk.checksum = format!("{:016x}", twox_hash::xxh3::hash64(&[0x42; 8]));
        }
        let mut destination = vec![0xA5; 64];
        let result = reader.read_into_with(&descriptor, &placement, &mut destination, &mock, None);
        if corrupt_retry {
            assert!(matches!(result, Err(RailReadError::Checksum { stripe: 0 })));
            assert_eq!(destination, vec![0xA5; 64]);
        } else {
            assert_eq!(result.unwrap(), 64);
            assert_eq!(destination, vec![0x42; 64]);
        }
        assert_eq!(mock.calls.lock().unwrap().len(), 3);
        assert_recovery_budget_empty(&reader);
    }
}

#[test]
fn recovery_deadline_is_checked_under_publication_gate_and_not_after_copy() {
    use std::sync::mpsc;
    let cancel = RailCancel::default();
    let guard = cancel.0.publish_gate.lock().unwrap();
    let deadline = Instant::now() + Duration::from_millis(20);
    let (entered_tx, entered_rx) = mpsc::channel();
    let published = AtomicBool::new(false);
    std::thread::scope(|scope| {
        let worker = scope.spawn(|| {
            entered_tx.send(()).unwrap();
            cancel.publish_before(Some(deadline), || published.store(true, Ordering::SeqCst))
        });
        entered_rx.recv_timeout(Duration::from_secs(5)).unwrap();
        while Instant::now() < deadline {
            std::thread::park_timeout(deadline.saturating_duration_since(Instant::now()));
        }
        drop(guard);
        assert!(matches!(
            worker.join().unwrap(),
            Err(RailReadError::DeadlineExceeded)
        ));
    });
    assert!(!published.load(Ordering::SeqCst));
    let deadline = Instant::now() + Duration::from_millis(10);
    cancel
        .publish_before(Some(deadline), || {
            published.store(true, Ordering::SeqCst);
            while Instant::now() < deadline {
                std::thread::park_timeout(deadline.saturating_duration_since(Instant::now()));
            }
        })
        .unwrap();
    assert!(
        published.load(Ordering::SeqCst),
        "a completed publication must not become a timeout error"
    );
}

#[test]
fn recovery_rejects_unrepresentable_deadline_instead_of_disabling_it() {
    assert!(RailReader::new(routes(), RailLimits::default())
        .unwrap()
        .with_recovery(RailRecoveryOptions {
            timeout: Duration::MAX
        })
        .is_err());
    assert!(RailReader::new(routes(), RailLimits::default())
        .unwrap()
        .with_recovery(RailRecoveryOptions {
            timeout: Duration::ZERO
        })
        .is_err());
    let mut reader = recovery_reader(false);
    // Simulate a clock-range overflow at request entry after configuration.
    reader.recovery = Some(RailRecoveryOptions {
        timeout: Duration::MAX,
    });
    assert!(matches!(
        reader.start_deadline(),
        Err(RailReadError::InvalidPlacement(_))
    ));
}

#[test]
fn recovery_does_not_relabel_legacy_zero_io_timeout_as_deadline_expiry() {
    for is_striped in [false, true] {
        for recovery_enabled in [false, true] {
            let mut reader = RailReader::new(
                routes(),
                RailLimits {
                    io_timeout: Duration::ZERO,
                    ..RailLimits::default()
                },
            )
            .unwrap();
            if recovery_enabled {
                reader = reader
                    .with_recovery(RailRecoveryOptions {
                        timeout: Duration::from_secs(5),
                    })
                    .unwrap();
            }
            let calls = AtomicU64::new(0);
            let mock = RecoveryScript(
                |_: &RailRoute, _: &RailTask, _: &pb::ObjectDescriptor, timeout: Duration| {
                    calls.fetch_add(1, Ordering::SeqCst);
                    assert!(timeout.is_zero());
                    Err(RailReadError::Transport(
                        "legacy transport rejects zero socket timeout".into(),
                    ))
                },
            );
            let (mut descriptor, placement) = fixture(64, 64);
            descriptor.is_striped = is_striped;
            assert!(matches!(
                reader.read_staged_with(&descriptor, &placement, &mock, None),
                Err(RailReadError::Transport(_))
            ));
            assert_eq!(calls.load(Ordering::SeqCst), 1);
            assert_recovery_budget_empty(&reader);
        }
    }
}
