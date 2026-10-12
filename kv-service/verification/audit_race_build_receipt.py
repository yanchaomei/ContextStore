"""Compare a Linux build receipt's source bytes with one Git commit."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def parse_sha_file(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text().splitlines():
        value, name = line.split("  ", 1)
        relative = Path(name)
        if (
            len(value) != 64
            or name in result
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ValueError(f"invalid or duplicate SHA-256 line in {path}")
        result[name] = value
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    parser.add_argument("--binary", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if len(args.commit) != 40:
        parser.error("pass a full Git commit SHA")
    before = args.receipt / "source-before.sha256"
    after = args.receipt / "source-after.sha256"
    if before.read_bytes() != after.read_bytes():
        raise ValueError("source changed during the release test build")
    snapshot_check = args.receipt / "source-after-snapshot.sha256"
    snapshot_root = args.receipt / "source-snapshot"
    if snapshot_root.exists() != snapshot_check.exists():
        raise ValueError("incomplete source snapshot receipt")
    if snapshot_root.exists() and (
        before.read_bytes() != snapshot_check.read_bytes()
    ):
        raise ValueError("source changed while the build snapshot was copied")
    if (args.receipt / "source-commit-label.txt").read_text().strip() != args.commit:
        raise ValueError("build host source-commit label differs from requested commit")
    source_hashes = parse_sha_file(before)
    if len(source_hashes) < 10:
        raise ValueError("source manifest unexpectedly short")
    for name, expected in source_hashes.items():
        if snapshot_root.exists():
            source = (snapshot_root / name).read_bytes()
            blob_id = subprocess.run(
                ["git", "hash-object", "--stdin"],
                input=source,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode().strip()
            committed_blob_id = subprocess.run(
                ["git", "rev-parse", f"{args.commit}:{name}"],
                cwd=args.repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout.decode().strip()
            if blob_id != committed_blob_id:
                raise ValueError(f"build snapshot differs from Git tree: {name}")
        else:
            # Older receipts did not preserve source bytes, so they still
            # require readable local Git blobs for an independent audit.
            source = subprocess.run(
                ["git", "show", f"{args.commit}:{name}"],
                cwd=args.repo,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=True,
            ).stdout
        if digest(source) != expected:
            raise ValueError(f"build host source differs from committed file: {name}")
    binary_hashes = parse_sha_file(args.receipt / "binary.sha256")
    if len(binary_hashes) != 1:
        raise ValueError("expected exactly one built test binary")
    binary_sha = next(iter(binary_hashes.values()))
    if args.binary and digest(args.binary.read_bytes()) != binary_sha:
        raise ValueError("supplied test binary differs from build output hash")
    if "Executable tests/rail_read_e2e.rs" not in (
        args.receipt / "build.stderr"
    ).read_text():
        raise ValueError("release E2E build did not emit its executable receipt")
    receipt_hashes = parse_sha_file(args.receipt / "receipt.sha256")
    for name, expected in receipt_hashes.items():
        path = Path(name)
        if not path.is_absolute():
            path = args.receipt / path
        if not path.exists() or digest(path.read_bytes()) != expected:
            raise ValueError(f"build receipt file hash differs: {name}")
    result = {
        "source_commit": args.commit,
        "source_files_matched_to_commit": len(source_hashes),
        "source_before_and_after_identical": True,
        "source_snapshot_git_blob_parity": snapshot_root.exists(),
        "binary_sha256": binary_sha,
        "rustc_version": (args.receipt / "rustc-version.txt").read_text().strip(),
        "cargo_version": (args.receipt / "cargo-version.txt").read_text().strip(),
        "auditor_sha256": digest(Path(__file__).read_bytes()),
        "source_manifest_sha256": digest(before.read_bytes()),
        "scope": "source-byte parity and binary hash for one release build; not a hermetic reproducible-build proof",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(args.output, len(source_hashes), "committed source files matched")


if __name__ == "__main__":
    main()
