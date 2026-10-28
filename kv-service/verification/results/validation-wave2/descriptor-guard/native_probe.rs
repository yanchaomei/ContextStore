//! Deferred RXE probe using only the current safe Rust client and a private 8 KiB buffer.
#![cfg(feature = "rdma")]

use contextstore_client_rs::rdma::{RdmaClient, RdmaClientConfig};
use contextstore_client_rs::{pb::ObjectDescriptor, KvClient};
use std::io::{Read, Write};
use std::net::{Shutdown, TcpListener, TcpStream};
use std::sync::mpsc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

fn required(name: &str) -> String {
    std::env::var(name).unwrap_or_else(|_| panic!("set {name} for the isolated fixture"))
}

fn take(stream: &mut TcpStream, count: usize) -> Vec<u8> {
    let mut bytes = vec![0; count];
    stream.read_exact(&mut bytes).expect("read control frame");
    bytes
}

fn take_string(stream: &mut TcpStream, frame: &mut Vec<u8>) {
    let size_bytes = take(stream, 2);
    let size = u16::from_le_bytes([size_bytes[0], size_bytes[1]]) as usize;
    frame.extend(size_bytes);
    frame.extend(take(stream, size));
}

fn read_exact_object(config: RdmaClientConfig, descriptor: &ObjectDescriptor, payload: &[u8]) {
    let mut client = RdmaClient::connect(config).expect("fresh RDMA connection");
    let mut target = vec![0xa5; payload.len()];
    let registered = client
        .register_buffer(&mut target)
        .expect("register target");
    let result = client
        .get_descriptor_into_detailed(descriptor, &registered, 0)
        .expect("normal descriptor GET")
        .expect("object exists");
    assert_eq!(result.bytes, 8192);
    assert_eq!(registered.as_slice(), payload);
}

#[tokio::test]
#[ignore = "requires a dedicated RXE fixture; run only after the parent grants the guest window"]
async fn descriptor_get_retirement_keeps_private_target_and_fresh_reads_correct() {
    let endpoint = required("CS_GUARD_RDMA_ENDPOINT");
    let config = RdmaClientConfig::new(&endpoint, required("CS_GUARD_RDMA_DEVICE"))
        .with_gid_index(required("CS_GUARD_RDMA_GID").parse().expect("GID index"))
        .with_io_timeout(Duration::from_secs(10));
    let mut grpc = KvClient::connect(required("CS_GUARD_GRPC_ENDPOINT"))
        .await
        .expect("isolated gRPC fixture");
    let key = format!(
        "descriptor-guard-{}",
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_nanos()
    );
    let payload: Vec<u8> = (0..8192).map(|index| (index % 251) as u8).collect();
    assert!(grpc
        .put("descriptor-guard", &key, payload.clone())
        .await
        .unwrap());
    let descriptor = grpc
        .lookup_object("descriptor-guard", &key)
        .await
        .unwrap()
        .unwrap()
        .descriptor;
    assert_eq!(descriptor.size, 8192);
    assert!(
        !descriptor.is_striped,
        "the fixture must keep 8 KiB objects unstriped"
    );
    read_exact_object(config.clone(), &descriptor, &payload);

    let listener = TcpListener::bind("127.0.0.1:0").expect("private proxy listener");
    let proxy_endpoint = listener.local_addr().unwrap().to_string();
    let (retired_tx, retired_rx) = mpsc::channel();
    let proxy = std::thread::spawn(move || {
        let (mut downstream, _) = listener.accept().expect("client connects to proxy");
        downstream
            .set_read_timeout(Some(Duration::from_secs(10)))
            .unwrap();
        let mut upstream = TcpStream::connect(endpoint).expect("isolated server connects");
        upstream
            .set_read_timeout(Some(Duration::from_secs(15)))
            .unwrap();
        upstream.write_all(&take(&mut downstream, 25)).unwrap();
        downstream.write_all(&take(&mut upstream, 25)).unwrap();
        let mut frame = take(&mut downstream, 1);
        assert_eq!(frame[0], 8, "only a full descriptor GET is intercepted");
        take_string(&mut downstream, &mut frame);
        take_string(&mut downstream, &mut frame);
        frame.extend(take(&mut downstream, 8));
        take_string(&mut downstream, &mut frame);
        let tail = take(&mut downstream, 49);
        assert_eq!(tail[16], 0, "descriptor must be unstriped");
        assert_eq!(u64::from_le_bytes(tail[41..49].try_into().unwrap()), 8192);
        frame.extend(tail);
        downstream.shutdown(Shutdown::Both).unwrap();
        // The fixed client returns only after destroying its QP. Keep the fully
        // registered target alive; forward no request until that return is observed.
        retired_rx
            .recv_timeout(Duration::from_secs(10))
            .expect("client QP retired");
        upstream
            .write_all(&frame)
            .expect("forward captured GET after retirement");
        let mut response = Vec::new();
        upstream
            .read_to_end(&mut response)
            .expect("server closes old connection");
        assert!(
            response.is_empty(),
            "retired GET must not send a success ACK"
        );
    });

    let mut faulted = RdmaClient::connect(RdmaClientConfig {
        endpoint: proxy_endpoint,
        ..config.clone()
    })
    .expect("connect via private proxy");
    let mut target = vec![0xa5; 8192];
    let registered = faulted
        .register_buffer(&mut target)
        .expect("fully register private target");
    let error = faulted
        .get_descriptor_into(&descriptor, &registered, 0)
        .unwrap_err();
    assert!(
        error
            .chain()
            .filter_map(|cause| cause.downcast_ref::<std::io::Error>())
            .any(|cause| cause.kind() == std::io::ErrorKind::UnexpectedEof),
        "controlled EOF: {error:#}"
    );
    retired_tx
        .send(())
        .expect("permit forwarding after fixed client return");
    proxy.join().expect("proxy observes server terminal state");
    assert!(registered.as_slice().iter().all(|byte| *byte == 0xa5));
    drop(registered);
    drop(faulted);
    read_exact_object(config, &descriptor, &payload);
    println!("{{\"private_target_bytes\":8192,\"controlled_eof\":true,\"target_unchanged_after_server_close\":true,\"fresh_exact_read_before\":true,\"fresh_exact_read_after\":true}}");
}
