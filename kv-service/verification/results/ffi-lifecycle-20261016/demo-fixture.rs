extern crate twox_hash;
fn main() {
    let size = 64usize * 1024 * 1024;
    let payload: Vec<u8> = (0..size).map(|index| (index % 251) as u8).collect();
    println!("fixture_bytes={}", payload.len());
    println!("fixture_twox_hash_1_6_3_xxh3={:016x}", twox_hash::xxh3::hash64(&payload));
}
