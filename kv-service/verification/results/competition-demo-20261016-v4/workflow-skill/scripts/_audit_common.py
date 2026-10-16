from __future__ import annotations

# Small shared primitives for local, non-destructive artifact audits.

import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import BinaryIO
import unicodedata

CHUNK_SIZE = 1024 * 1024
RESERVED_WINDOWS = {"CON", "PRN", "AUX", "NUL"} | {
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
}


class AuditError(ValueError):
    """An input or integrity check failed."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AuditError(message)


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        require(key not in result, "duplicate JSON object key")
        result[key] = value
    return result


def _invalid_number(value: str) -> None:
    raise AuditError("non-finite JSON number")


def parse_json(data: str | bytes) -> object:
    return json.loads(data, object_pairs_hook=_unique_object, parse_constant=_invalid_number)


def json_bytes(data: object) -> bytes:
    return (json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode("utf-8")


def read_json(path: Path) -> dict:
    data = parse_json(path.read_bytes())
    require(isinstance(data, dict), "JSON root must be an object")
    return data


def path_key(path: str) -> str:
    return unicodedata.normalize("NFC", path).casefold()


def safe_relative(value: object) -> str:
    require(isinstance(value, str) and bool(value), "relative path must be a nonempty string")
    require(
        not any(unicodedata.category(c) in {"Cc", "Cf", "Cs"} for c in value),
        "control or surrogate character in relative path",
    )
    require(not re.search(r'[\\:*?"<>|]', value), "unsafe character in relative path")
    pieces = value.split("/")
    require(all(p not in {"", ".", ".."} for p in pieces), "absolute or traversal path")
    for piece in pieces:
        require(piece == piece.rstrip(" ."), "path component ends with a space or dot")
        require(
            piece.split(".")[0].upper() not in RESERVED_WINDOWS, "reserved Windows path component"
        )
    return value


def unique_paths(names: list[str]) -> None:
    keys = [path_key(safe_relative(name)) for name in names]
    require(len(keys) == len(set(keys)), "duplicate or normalized archive path collision")
    all_keys = set(keys)
    for key in keys:
        components = key.split("/")
        require(
            not any("/".join(components[:i]) in all_keys for i in range(1, len(components))),
            "file/directory archive path collision",
        )


def rooted_file(root: Path, relative: object) -> Path:
    relative = safe_relative(relative)
    candidate = root
    for part in relative.split("/"):
        candidate = candidate / part
        require(not candidate.is_symlink(), "source symlinks are not allowed")
    require(candidate.is_file(), f"missing or non-regular source file: {relative}")
    require(candidate.resolve().is_relative_to(root), "source escapes the selected root")
    return candidate


def digest_stream(stream: BinaryIO) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(CHUNK_SIZE):
        size += len(chunk)
        digest.update(chunk)
    return digest.hexdigest(), size


def digest_file(path: Path) -> tuple[str, int]:
    with path.open("rb") as stream:
        return digest_stream(stream)


def valid_sha256(value: object) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-f]{64}", value))


def new_output(path: Path) -> Path:
    require(not path.is_symlink() and not path.exists(), "output already exists")
    parent = path.parent.resolve(strict=True)
    require(parent.is_dir(), "output parent must be an existing directory")
    target = parent / path.name
    require(not target.exists() and not target.is_symlink(), "output already exists")
    return target


def distinct_outputs(paths: list[Path], inputs: list[Path]) -> None:
    keys = [path_key(str(path)) for path in paths]
    require(len(keys) == len(set(keys)), "output paths collide")
    require(
        not set(keys) & {path_key(str(path.resolve())) for path in inputs},
        "output path collides with an input",
    )


def publish_new(temporary: Path, target: Path) -> None:
    """Publish a staged file without replacing a concurrently created target."""
    os.link(temporary, target)
    temporary.unlink()


def write_json_new(path: Path, data: object) -> None:
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(json_bytes(data))
            stream.flush()
            os.fsync(stream.fileno())
        publish_new(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
