from __future__ import annotations

# Exercise artifact helper contracts with isolated synthetic fixtures.

import base64
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import wave
import warnings
import zipfile

BASE = Path(__file__).resolve().parent
CASES = BASE / "cases" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
CASES.mkdir()
SKILL = Path("/Users/yanchaomei/.codex/skills/competition-submission-studio")
SCRIPTS = SKILL / "scripts"
RESULTS = []


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def run_case(name: str, args: list[str], expected: int) -> subprocess.CompletedProcess:
    process = subprocess.run(
        [sys.executable, "-B", *args], capture_output=True, text=True, timeout=30
    )
    RESULTS.append(
        {
            "name": name,
            "command": [sys.executable, "-B", *args],
            "expected_exit": expected,
            "actual_exit": process.returncode,
            "stdout": process.stdout,
            "stderr": process.stderr,
            "passed": process.returncode == expected,
        }
    )
    if process.returncode != expected:
        raise AssertionError(f"{name}: {process.returncode}, {process.stderr}")
    return process


def spec_case(name: str, rows: list[dict], expected: int = 2) -> Path:
    case = CASES / name
    case.mkdir()
    source = case / "root"
    source.mkdir()
    (source / "report.txt").write_text("Synthetic report fixture.\n")
    (source / "evidence.json").write_text('{"observed": true}\n')
    spec = case / "package.json"
    write_json(spec, {"schema_version": 1, "files": rows})
    run_case(
        name,
        [
            str(SCRIPTS / "package_audit.py"),
            "build",
            "--root",
            str(source),
            "--spec",
            str(spec),
            "--zip",
            str(case / "submission.zip"),
            "--audit",
            str(case / "audit.json"),
        ],
        expected,
    )
    if expected:
        assert not (case / "submission.zip").exists()
        assert not (case / "audit.json").exists()
    return case


def verify_case(name: str, archive: Path, expected: int, extra: list[str] | None = None) -> None:
    case = CASES / name
    case.mkdir()
    run_case(
        name,
        [
            str(SCRIPTS / "package_audit.py"),
            "verify",
            "--zip",
            str(archive),
            "--audit",
            str(case / "audit.json"),
            *(extra or []),
        ],
        expected,
    )
    if expected:
        assert not (case / "audit.json").exists()


def narration_case(
    name: str,
    segments: list[dict],
    expected: int,
    total: object | None = None,
    extra: list[str] | None = None,
) -> dict | None:
    case = CASES / name
    case.mkdir()
    data = {"schema_version": 1, "segments": segments}
    if total is not None:
        data["duration_seconds"] = total
    scene_file = case / "scenes.json"
    write_json(scene_file, data)
    run_case(
        name,
        [
            str(SCRIPTS / "check_narration.py"),
            "--root",
            str(AUDIO),
            "--scenes",
            str(scene_file),
            "--output",
            str(case / "audit.json"),
            *(extra or []),
        ],
        expected,
    )
    result = case / "audit.json"
    if expected == 2:
        assert not result.exists()
        return None
    audit = json.loads(result.read_text())
    assert audit["audio_modified_by_checker"] is False
    for field in (
        "subjective_audio_review",
        "spoken_text_completeness",
        "audio_visual_sync",
        "provider_identity",
        "publication_status",
    ):
        assert audit[field] == "not_assessed"
    return audit


try:
    good = spec_case(
        "package-success",
        [
            {"source": "report.txt", "archive": "作品说明.txt"},
            {"source": "evidence.json", "archive": "evidence/results.json"},
        ],
        0,
    )
    good_audit = json.loads((good / "audit.json").read_text())
    assert good_audit["archive_integrity"] == "pass"
    for field in (
        "content_review",
        "visual_review",
        "subjective_audio_review",
        "publication_status",
        "submission_status",
    ):
        assert good_audit[field] == "not_assessed"
    assert good_audit["secret_scan"] == "not_performed"
    with zipfile.ZipFile(good / "submission.zip") as archive:
        assert set(archive.namelist()) == {
            "作品说明.txt",
            "evidence/results.json",
            "__submission__/manifest.json",
            "__submission__/SHA256SUMS",
        }
        for row in good_audit["payload_files"]:
            assert hashlib.sha256(archive.read(row["archive"])).hexdigest() == row["sha256"]
    verify_case("package-independent-readback", good / "submission.zip", 0)
    run_case(
        "package-deterministic-rebuild",
        [
            str(SCRIPTS / "package_audit.py"),
            "build",
            "--root",
            str(good / "root"),
            "--spec",
            str(good / "package.json"),
            "--zip",
            str(good / "rebuild.zip"),
            "--audit",
            str(good / "rebuild-audit.json"),
        ],
        0,
    )
    assert (good / "submission.zip").read_bytes() == (good / "rebuild.zip").read_bytes()
    run_case(
        "package-refuses-existing-output",
        [
            str(SCRIPTS / "package_audit.py"),
            "build",
            "--root",
            str(good / "root"),
            "--spec",
            str(good / "package.json"),
            "--zip",
            str(good / "submission.zip"),
            "--audit",
            str(good / "unused.json"),
        ],
        2,
    )

    expected_sha = hashlib.sha256(b"Synthetic report fixture.\n").hexdigest()
    spec_case(
        "package-frozen-hash-success",
        [
            {"source": "report.txt", "archive": "report.txt", "sha256": expected_sha},
        ],
        0,
    )
    spec_case("package-missing-source", [{"source": "missing.pdf", "archive": "report.pdf"}])
    spec_case(
        "package-source-hash-mismatch",
        [
            {"source": "report.txt", "archive": "report.txt", "sha256": "0" * 64},
        ],
    )
    spec_case(
        "package-invalid-expected-hash",
        [
            {"source": "report.txt", "archive": "report.txt", "sha256": "invalid"},
        ],
    )
    spec_case(
        "package-duplicate-archive-path",
        [
            {"source": "report.txt", "archive": "report.txt"},
            {"source": "evidence.json", "archive": "report.txt"},
        ],
    )
    spec_case(
        "package-casefold-collision",
        [
            {"source": "report.txt", "archive": "Report.txt"},
            {"source": "evidence.json", "archive": "report.txt"},
        ],
    )
    spec_case(
        "package-unicode-collision",
        [
            {"source": "report.txt", "archive": "caf\u00e9.txt"},
            {"source": "evidence.json", "archive": "cafe\u0301.txt"},
        ],
    )
    spec_case(
        "package-prefix-collision",
        [
            {"source": "report.txt", "archive": "evidence"},
            {"source": "evidence.json", "archive": "evidence/results.json"},
        ],
    )
    for name, archive_name in [
        ("package-archive-traversal", "../outside.txt"),
        ("package-archive-absolute", "/outside.txt"),
        ("package-archive-backslash", "folder\\file.txt"),
        ("package-archive-windows-device", "CON.txt"),
        ("package-archive-control", "bad\nname.txt"),
        ("package-reserved-metadata", "__submission__/payload.txt"),
        ("package-trailing-dot", "report.txt."),
    ]:
        spec_case(name, [{"source": "report.txt", "archive": archive_name}])
    spec_case("package-source-traversal", [{"source": "../outside.txt", "archive": "report.txt"}])
    spec_case("package-source-absolute", [{"source": "/etc/hosts", "archive": "report.txt"}])
    symlinks = CASES / "package-source-symlinks"
    symlinks.mkdir()
    source_root = symlinks / "root"
    source_root.mkdir()
    (symlinks / "outside.txt").write_text("Outside source root.\n")
    (source_root / "link.txt").symlink_to(symlinks / "outside.txt")
    (source_root / "parent-link").symlink_to(symlinks, target_is_directory=True)
    for name, source_name in [
        ("source-file-symlink", "link.txt"),
        ("source-parent-symlink", "parent-link/outside.txt"),
    ]:
        spec = symlinks / f"{name}.json"
        write_json(
            spec,
            {
                "schema_version": 1,
                "files": [
                    {"source": source_name, "archive": "report.txt"},
                ],
            },
        )
        run_case(
            name,
            [
                str(SCRIPTS / "package_audit.py"),
                "build",
                "--root",
                str(source_root),
                "--spec",
                str(spec),
                "--zip",
                str(symlinks / f"{name}.zip"),
                "--audit",
                str(symlinks / f"{name}-audit.json"),
            ],
            2,
        )
    run_case(
        "package-output-collision",
        [
            str(SCRIPTS / "package_audit.py"),
            "build",
            "--root",
            str(good / "root"),
            "--spec",
            str(good / "package.json"),
            "--zip",
            str(good / "same-output"),
            "--audit",
            str(good / "same-output"),
        ],
        2,
    )

    corrupt_root = CASES / "corrupt-inputs"
    corrupt_root.mkdir()
    for kind in ("payload-tamper", "sums-tamper", "missing-manifest", "duplicate-member"):
        target = corrupt_root / f"{kind}.zip"
        with zipfile.ZipFile(good / "submission.zip") as src:
            with zipfile.ZipFile(target, "w") as dst:
                for info in src.infolist():
                    if (
                        kind == "missing-manifest"
                        and info.filename == "__submission__/manifest.json"
                    ):
                        continue
                    content = src.read(info.filename)
                    if kind == "payload-tamper" and info.filename == "作品说明.txt":
                        content = b"X" * len(content)
                    if kind == "sums-tamper" and info.filename == "__submission__/SHA256SUMS":
                        content = b"0" + content[1:]
                    dst.writestr(info, content)
                if kind == "duplicate-member":
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore", UserWarning)
                        dst.writestr("作品说明.txt", b"duplicate")
        verify_case(f"package-rejects-{kind}", target, 2)
    verify_case(
        "package-uncompressed-limit", good / "submission.zip", 2, ["--max-uncompressed-bytes", "1"]
    )

    AUDIO = CASES / "audio-fixtures"
    AUDIO.mkdir()
    audio_path = AUDIO / "one-second.wav"
    with wave.open(str(audio_path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(16000)
        stream.writeframes(
            b"".join(
                struct.pack("<h", int(1000 * math.sin(2 * math.pi * 220 * i / 16000)))
                for i in range(16000)
            )
        )
    audio_before = audio_path.read_bytes()
    (AUDIO / "not-audio.txt").write_text("This is not an audio file.\n")
    (AUDIO / "pixel.png").write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jvXkAAAAASUVORK5CYII="
        )
    )
    (AUDIO / "link.wav").symlink_to(audio_path)
    scene = {
        "id": "fixture",
        "duration_seconds": 2,
        "audio": "one-second.wav",
        "narration": "Synthetic duration fixture; no speech or listening claim.",
    }
    audit = narration_case("narration-fits", [scene], 0, 2)
    assert audit["segments"][0]["audio_duration_seconds"] == 1.0
    assert audit["all_whole_files_fit"] is True
    overrun = narration_case("narration-overrun", [{**scene, "duration_seconds": 1.2}], 1, 1.2)
    assert overrun["all_whole_files_fit"] is False
    assert overrun["segments"][0]["overrun_seconds"] > 0
    narration_case("narration-lead-in-overrun", [{**scene, "lead_in_seconds": 0.8}], 1)
    narration_case("narration-missing-file", [{**scene, "audio": "missing.wav"}], 2)
    narration_case("narration-invalid-media", [{**scene, "audio": "not-audio.txt"}], 2)
    narration_case("narration-no-audio-stream", [{**scene, "audio": "pixel.png"}], 2)
    narration_case("narration-source-symlink", [{**scene, "audio": "link.wav"}], 2)
    narration_case("narration-source-traversal", [{**scene, "audio": "../outside.wav"}], 2)
    narration_case(
        "narration-no-ffprobe", [scene], 2, extra=["--ffprobe", str(AUDIO / "no-ffprobe")]
    )
    narration_case("narration-duplicate-id", [scene, scene], 2)
    narration_case("narration-total-mismatch", [scene], 2, 3)
    narration_case("narration-duration-conflict", [{**scene, "duration": 3}], 2)
    narration_case("narration-negative-duration", [{**scene, "duration_seconds": -1}], 2)
    narration_case("narration-nonfinite-duration", [{**scene, "duration_seconds": float("nan")}], 2)
    narration_case(
        "narration-legacy-duration",
        [
            {"id": "legacy", "duration": 2, "audio": "one-second.wav"},
        ],
        0,
    )
    narration_case(
        "narration-aggregate-duration-overflow",
        [
            {**scene, "id": "huge-one", "duration_seconds": 1e308},
            {**scene, "id": "huge-two", "duration_seconds": 1e308},
        ],
        2,
    )
    assert json.loads(RESULTS[-1]["stderr"])["status"] == "error"
    assert "Traceback" not in RESULTS[-1]["stderr"]
    malformed_probe = AUDIO / "malformed-ffprobe"
    malformed_probe.write_text(
        '#!/bin/sh\nprintf \'%s\\n\' \'{"streams":[null],"format":{"duration":"1"}}\'\n'
    )
    malformed_probe.chmod(0o755)
    narration_case(
        "narration-malformed-probe-stream",
        [scene],
        2,
        extra=["--ffprobe", str(malformed_probe)],
    )
    assert json.loads(RESULTS[-1]["stderr"])["status"] == "error"
    assert "Traceback" not in RESULTS[-1]["stderr"]
    assert audio_path.read_bytes() == audio_before
    overall = "pass"
except Exception as exc:
    overall = "fail"
    RESULTS.append(
        {
            "name": "validation-harness-assertion",
            "passed": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
    )
finally:
    hashes = {
        str(path.relative_to(SKILL)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(SKILL.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts
    }
    report = {
        "date_utc": datetime.now(timezone.utc).isoformat(),
        "overall": overall,
        "case_count": len(RESULTS),
        "case_root": str(CASES),
        "all_cases_passed": all(result["passed"] for result in RESULTS),
        "cases": RESULTS,
        "skill_file_sha256": hashes,
        "actual_audio_fixture": "One-second synthetic sine-wave WAV; not synthesized speech.",
        "not_performed": [
            "online provider synthesis",
            "subjective audio review",
            "visual QA",
            "publication",
            "submission",
            "independent forward-test",
        ],
        "scope": "Local helper behavior and input rejection, not finished competition materials.",
    }
    write_json(BASE / "validation-report.json", report)
print(json.dumps({"overall": overall, "case_count": len(RESULTS)}, ensure_ascii=False))
raise SystemExit(0 if overall == "pass" else 1)
