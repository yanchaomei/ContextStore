#!/usr/bin/env python3
"""Replay the Python integer transcription actually used in the pilot audit.

Derived from twox-hash 1.6.3, src/xxh3.rs (long-input hash64, generic
accumulate512/scramble_acc, merge_accs and avalanche), with prime constants
from src/sixty_four.rs and src/thirty_two.rs. Original source is preserved
beside this script in twox-hash-1.6.3/. The source is MIT licensed:
Copyright (c) 2015 Jake Goulding. This derived transcription retains that
license; the complete notice is twox-hash-1.6.3/LICENSE.txt.

This is the same scalar logic and five known-answer vectors executed in the
audit. Packaging changes only: functions/main entry point and output path.
It intentionally supports only zero-seed inputs longer than 240 bytes.
No Rust build, network access, external Python dependency, or benchmark run.
"""
from pathlib import Path
import argparse
import datetime
import hashlib
import json
import re
import struct

HERE = Path(__file__).resolve().parent
SOURCE = HERE / 'twox-hash-1.6.3/src/xxh3.rs'
source_text = SOURCE.read_text()
secret = bytes(int(v, 16) for v in re.findall(
    r'0x([0-9a-f]+)', source_text.split('const SECRET: Secret = Secret([')[1].split(']);')[0]))
assert len(secret) == 192
M = (1 << 64) - 1
P1 = 11400714785074694791
P2 = 14029467366897019727
P3 = 1609587929392839161
P4 = 9650029242287828579
P5 = 2870177450012600261
P32 = 2654435761
keys = [struct.unpack_from('<8Q', secret, 8 * r) for r in range(16)]
sk = struct.unpack_from('<8Q', secret, 128)


def h(data):
    n = len(data)
    assert n > 240
    acc = [3266489917, P1, P2, P3, P4, 2246822519, P5, P32]

    def stripe(offset, key):
        vals = struct.unpack_from('<8Q', data, offset)
        for lane, (v, k) in enumerate(zip(vals, key)):
            x = v ^ k
            acc[lane] = (acc[lane] + ((x & 0xffffffff) * (x >> 32)) + v) & M

    for block in range(n // 1024):
        for r, key in enumerate(keys):
            stripe(block * 1024 + r * 64, key)
        for lane, k in enumerate(sk):
            v = acc[lane]
            acc[lane] = ((v ^ (v >> 47) ^ k) * P32) & M
    start = n // 1024 * 1024
    for r in range((n % 1024) // 64):
        stripe(start + r * 64, keys[r])
    if n % 64:
        stripe(n - 64, struct.unpack_from('<8Q', secret, 121))
    merged = (n * P1) & M
    for lane in range(0, 8, 2):
        k0, k1 = struct.unpack_from('<2Q', secret, 11 + 8 * lane)
        mul = (acc[lane] ^ k0) * (acc[lane + 1] ^ k1)
        merged = (merged + ((mul & M) ^ (mul >> 64))) & M
    merged ^= merged >> 37
    merged = (merged * P3) & M
    merged ^= merged >> 32
    return f'{merged:016x}'


def compute_receipt():
    gen = 2654435761
    buf = bytearray()
    for _ in range(2243):
        buf.append(gen >> 56)
        gen = (gen * 11400714785074694797) & M
    cases = {403: '4834389b15d981e8', 512: '6a1b982631f059a8',
             2048: 'efefd4449323cdd4', 2240: '998c0437486672c7',
             2243: 'a559d20581d742d3'}
    for n, want in cases.items():
        assert h(buf[:n]) == want, (n, h(buf[:n]), want)
    results = {}
    for size in (16, 64):
        n = size * 1024 * 1024
        buf = bytes(range(251)) * (n // 251) + bytes(range(n % 251))
        results[size] = h(buf)
    assert results == {16: '614d9758e87638c7', 64: 'a0a4cbfa5cad46af'}
    return {
        'status': 'PASS',
        'audit_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'method': 'Independent Python integer transcription of twox-hash 1.6.3 long-input 64-bit hash; secret extracted from copied pinned crate source; explicit 64-bit wrapping, scalar accumulate/scramble/merge; verified 5 existing crate long-input known-answer vectors before deterministic payloads. No Rust build or benchmark run.',
        'crate_source_sha256': hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        'source_reference': 'twox-hash-1.6.3/src/xxh3.rs: long-key loop, generic scalar accumulate512/scramble_acc, merge_accs, avalanche',
        'known_answer_vectors_passed': cases,
        'deterministic_pattern_i_mod_251_hashes': results,
        'modern_xxhash_mismatch_resolution': 'twox-hash 1.6.3 documents an in-progress historical XXH3 algorithm; modern xxhash.xxh3_64 is not a compatible oracle. Independent transcription reproduces both pilot expected hashes exactly.',
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=HERE / 'replay/pilot-hash-compatibility.json')
    args = parser.parse_args()
    receipt = compute_receipt()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + '\n')
    print(json.dumps({'status': receipt['status'], 'hashes': receipt['deterministic_pattern_i_mod_251_hashes'], 'output': str(args.output)}, indent=2))
