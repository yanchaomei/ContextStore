from __future__ import annotations

# Build deterministic submission ZIPs and independently audit their payload bytes.

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import zipfile
import zlib

from _audit_common import (
    AuditError,
    digest_file,
    digest_stream,
    distinct_outputs,
    json_bytes,
    new_output,
    parse_json,
    path_key,
    publish_new,
    read_json,
    require,
    rooted_file,
    safe_relative,
    unique_paths,
    valid_sha256,
    write_json_new,
)

INTERNAL_ROOT = "__submission__"
MANIFEST_NAME = f"{INTERNAL_ROOT}/manifest.json"
SUMS_NAME = f"{INTERNAL_ROOT}/SHA256SUMS"
MAX_MANIFEST_BYTES = 10 * 1024 * 1024
DEFAULT_MAX_BYTES = 20 * 1024**3


def checksums(entries: list[dict]) -> bytes:
    return "".join(f'{entry["sha256"]}  {entry["archive"]}\n' for entry in entries).encode("utf-8")


def payload_names(entries: list[dict]) -> list[str]:
    require(bool(entries), "at least one payload file is required")
    names = []
    for entry in entries:
        require(isinstance(entry, dict), "file entry must be an object")
        name = safe_relative(entry.get("archive"))
        require(path_key(name.split("/")[0]) != INTERNAL_ROOT, "reserved internal archive path")
        names.append(name)
    unique_paths(names)
    return names


def load_sources(root: Path, spec: dict) -> tuple[dict, list[Path]]:
    require(
        type(spec.get("schema_version")) is int and spec["schema_version"] == 1,
        "unsupported package specification schema",
    )
    rows = spec.get("files")
    require(isinstance(rows, list), "files must be a list")
    payload_names(rows)
    metadata = spec.get("metadata", {})
    require(isinstance(metadata, dict), "metadata must be an object")
    entries = []
    sources = []
    for row in rows:
        source = rooted_file(root, row.get("source"))
        actual_sha, size = digest_file(source)
        if "sha256" in row:
            require(valid_sha256(row["sha256"]), "invalid expected SHA-256")
            require(row["sha256"] == actual_sha, f'source SHA-256 mismatch: {row["source"]}')
        entries.append(
            {
                "source": row["source"],
                "archive": row["archive"],
                "sha256": actual_sha,
                "size_bytes": size,
            }
        )
        sources.append(source)
    entries.sort(key=lambda entry: entry["archive"])
    return {"schema_version": 1, "metadata": metadata, "files": entries}, sources


def zip_info(name: str) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.create_system = 3
    info.external_attr = (stat.S_IFREG | 0o644) << 16
    info.compress_type = zipfile.ZIP_DEFLATED
    return info


def verify_archive(path: Path, max_bytes: int) -> dict:
    require(max_bytes > 0, "maximum uncompressed size must be positive")
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        names = [info.filename for info in infos]
        unique_paths(names)
        require(
            sum(info.file_size for info in infos) <= max_bytes,
            "archive exceeds maximum uncompressed size",
        )
        for info in infos:
            mode = info.external_attr >> 16
            require(not info.is_dir(), "directory entries are not accepted")
            require(stat.S_IFMT(mode) in {0, stat.S_IFREG}, "non-regular archive member")
            require(not info.flag_bits & 1, "encrypted archive members are not accepted")
        require(MANIFEST_NAME in names and SUMS_NAME in names, "internal manifest or sums missing")
        require(
            archive.getinfo(MANIFEST_NAME).file_size <= MAX_MANIFEST_BYTES,
            "internal manifest exceeds size limit",
        )
        manifest_bytes = archive.read(MANIFEST_NAME)
        manifest = parse_json(manifest_bytes)
        require(isinstance(manifest, dict), "internal manifest must be an object")
        require(
            type(manifest.get("schema_version")) is int and manifest["schema_version"] == 1,
            "unsupported internal manifest schema",
        )
        entries = manifest.get("files")
        require(isinstance(entries, list), "manifest files must be a list")
        expected = payload_names(entries)
        require(
            set(names) == set(expected) | {MANIFEST_NAME, SUMS_NAME},
            "archive member set differs from manifest",
        )
        require(
            entries == sorted(entries, key=lambda entry: entry["archive"]),
            "manifest entries must be sorted",
        )
        for entry in entries:
            require(valid_sha256(entry.get("sha256")), "invalid manifest SHA-256")
            size = entry.get("size_bytes")
            require(type(size) is int and size >= 0, "invalid manifest file size")
            info = archive.getinfo(entry["archive"])
            require(info.file_size == size, f'member size mismatch: {entry["archive"]}')
            with archive.open(info) as stream:
                actual_sha, actual_size = digest_stream(stream)
            require(
                actual_size == size and actual_sha == entry["sha256"],
                f'member SHA-256 mismatch: {entry["archive"]}',
            )
        sums = checksums(entries)
        require(archive.getinfo(SUMS_NAME).file_size == len(sums), "invalid checksum list size")
        require(archive.read(SUMS_NAME) == sums, "internal checksum list differs from manifest")
    zip_sha, zip_size = digest_file(path)
    return {
        "schema_version": 1,
        "scope": "payload bytes, archive membership, paths, sizes and hashes only",
        "archive_integrity": "pass",
        "payload_files": entries,
        "zip_sha256": zip_sha,
        "zip_size_bytes": zip_size,
        "internal_manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
        "content_review": "not_assessed",
        "visual_review": "not_assessed",
        "subjective_audio_review": "not_assessed",
        "publication_status": "not_assessed",
        "submission_status": "not_assessed",
        "secret_scan": "not_performed",
    }


def build(args: argparse.Namespace) -> dict:
    root = args.root.resolve(strict=True)
    require(root.is_dir(), "root must be a directory")
    manifest, sources = load_sources(root, read_json(args.spec))
    total = sum(row["size_bytes"] for row in manifest["files"])
    require(total <= args.max_uncompressed_bytes, "payload exceeds maximum uncompressed size")
    target = new_output(args.zip)
    audit_path = new_output(args.audit)
    distinct_outputs([target, audit_path], sources + [args.spec])
    temporary = None
    try:
        handle, temporary_name = tempfile.mkstemp(dir=target.parent, suffix=".zip")
        os.close(handle)
        temporary = Path(temporary_name)
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for entry in manifest["files"]:
                with rooted_file(root, entry["source"]).open("rb") as src:
                    with archive.open(zip_info(entry["archive"]), "w", force_zip64=True) as dst:
                        shutil.copyfileobj(src, dst, length=1024 * 1024)
            archive.writestr(zip_info(MANIFEST_NAME), json_bytes(manifest))
            archive.writestr(zip_info(SUMS_NAME), checksums(manifest["files"]))
        audit = verify_archive(temporary, args.max_uncompressed_bytes)
        audit["operation"] = "build_and_readback"
        publish_new(temporary, target)
        write_json_new(audit_path, audit)
        return audit
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def verify(args: argparse.Namespace) -> dict:
    archive = args.zip.resolve(strict=True)
    require(archive.is_file(), "ZIP must be a regular file")
    audit_path = new_output(args.audit)
    distinct_outputs([audit_path], [archive])
    audit = verify_archive(archive, args.max_uncompressed_bytes)
    audit["operation"] = "independent_readback"
    write_json_new(audit_path, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build a submission ZIP or independently audit its paths and payload hashes."
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("build", "verify"):
        command = commands.add_parser(name)
        command.add_argument("--zip", type=Path, required=True)
        command.add_argument("--audit", type=Path, required=True)
        command.add_argument("--max-uncompressed-bytes", type=int, default=DEFAULT_MAX_BYTES)
        if name == "build":
            command.add_argument("--root", type=Path, required=True)
            command.add_argument("--spec", type=Path, required=True)
    args = parser.parse_args()
    try:
        audit = build(args) if args.command == "build" else verify(args)
    except (
        AuditError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        zipfile.BadZipFile,
        RuntimeError,
        zlib.error,
    ) as exc:
        print(
            json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr
        )
        return 2
    print(
        json.dumps(
            {
                "status": "pass",
                "operation": audit["operation"],
                "payload_count": len(audit["payload_files"]),
                "zip_sha256": audit["zip_sha256"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
