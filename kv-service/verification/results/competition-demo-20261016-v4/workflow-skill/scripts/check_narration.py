from __future__ import annotations

# Measure whole narration files with ffprobe without changing audio or playback rate.

import argparse
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

from _audit_common import (
    AuditError,
    digest_file,
    distinct_outputs,
    new_output,
    parse_json,
    read_json,
    require,
    rooted_file,
    write_json_new,
)


def number(value: object, label: str, positive: bool = False) -> float:
    require(type(value) in {int, float}, f"{label} must be a number")
    try:
        result = float(value)
    except OverflowError:
        raise AuditError(f"{label} is too large") from None
    require(
        math.isfinite(result) and (result > 0 if positive else result >= 0),
        f"{label} must be finite and {'positive' if positive else 'nonnegative'}",
    )
    return result


def probe(path: Path, ffprobe: str) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    require(result.returncode == 0, "ffprobe could not inspect an audio file")
    info = parse_json(result.stdout)
    require(isinstance(info, dict), "ffprobe returned invalid data")
    streams = info.get("streams", [])
    require(isinstance(streams, list) and streams, "no media streams found")
    require(
        all(isinstance(stream, dict) for stream in streams), "ffprobe returned an invalid stream"
    )
    require(any(stream.get("codec_type") == "audio" for stream in streams), "no audio stream found")
    require(
        not any(stream.get("codec_type") == "video" for stream in streams),
        "narration input contains a video stream",
    )
    try:
        duration = float(info["format"]["duration"])
    except (KeyError, TypeError, ValueError):
        raise AuditError("ffprobe did not report a usable duration") from None
    return number(duration, "audio duration", positive=True)


def check(args: argparse.Namespace) -> dict:
    root = args.root.resolve(strict=True)
    require(root.is_dir(), "root must be a directory")
    scenes = read_json(args.scenes)
    if "schema_version" in scenes:
        require(
            type(scenes["schema_version"]) is int and scenes["schema_version"] == 1,
            "unsupported scene schema",
        )
    segments = scenes.get("segments")
    require(isinstance(segments, list) and bool(segments), "segments must be a nonempty list")
    margin = number(args.margin_seconds, "tail margin")
    output = new_output(args.output)
    paths = []
    ids = set()
    rows = []
    for scene in segments:
        require(isinstance(scene, dict), "segment must be an object")
        scene_id = scene.get("id")
        require(isinstance(scene_id, str) and bool(scene_id.strip()), "segment ID must be nonempty")
        require(scene_id not in ids, "duplicate segment ID")
        ids.add(scene_id)
        duration = number(
            scene.get("duration_seconds", scene.get("duration")), "scene duration", positive=True
        )
        if "duration_seconds" in scene and "duration" in scene:
            require(
                number(scene["duration"], "legacy scene duration", positive=True) == duration,
                "conflicting duration fields in scene",
            )
        lead_in = number(scene.get("lead_in_seconds", 0), "scene lead-in")
        require(duration > lead_in + margin, "scene has no positive narration budget")
        audio = rooted_file(root, scene.get("audio"))
        paths.append(audio)
        audio_sha, audio_size = digest_file(audio)
        actual = probe(audio, args.ffprobe)
        require(digest_file(audio) == (audio_sha, audio_size), "audio changed during inspection")
        narration = scene.get("narration")
        require(narration is None or isinstance(narration, str), "narration must be text")
        text_sha = hashlib.sha256(narration.encode()).hexdigest() if narration is not None else None
        budget = duration - lead_in - margin
        rows.append(
            {
                "id": scene_id,
                "audio": scene["audio"],
                "audio_sha256": audio_sha,
                "audio_size_bytes": audio_size,
                "narration_text_sha256": text_sha,
                "scene_duration_seconds": duration,
                "lead_in_seconds": lead_in,
                "tail_margin_seconds": margin,
                "narration_budget_seconds": budget,
                "audio_duration_seconds": actual,
                "whole_file_fits": actual <= budget,
                "overrun_seconds": max(0.0, actual - budget),
            }
        )
    total = number(
        sum(row["scene_duration_seconds"] for row in rows), "total scene duration", positive=True
    )
    if "duration_seconds" in scenes:
        declared = number(scenes["duration_seconds"], "total duration", positive=True)
        require(
            math.isclose(declared, total, rel_tol=0, abs_tol=0.001),
            "declared duration differs from sum of scenes",
        )
    distinct_outputs([output], paths + [args.scenes])
    audit = {
        "schema_version": 1,
        "scope": "whole-file media durations and hashes only",
        "scenes_sha256": digest_file(args.scenes)[0],
        "scene_count": len(rows),
        "total_scene_duration_seconds": total,
        "all_whole_files_fit": all(row["whole_file_fits"] for row in rows),
        "segments": rows,
        "audio_modified_by_checker": False,
        "prior_audio_processing": "not_assessed",
        "spoken_text_completeness": "not_assessed",
        "subjective_audio_review": "not_assessed",
        "audio_visual_sync": "not_assessed",
        "provider_identity": "not_assessed",
        "publication_status": "not_assessed",
    }
    write_json_new(output, audit)
    return audit


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check narration durations with ffprobe; never modify audio or playback rate."
    )
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--scenes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffprobe", default="ffprobe")
    parser.add_argument("--margin-seconds", type=float, default=0.3)
    args = parser.parse_args()
    try:
        audit = check(args)
    except (
        AuditError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
        subprocess.TimeoutExpired,
    ) as exc:
        print(
            json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False), file=sys.stderr
        )
        return 2
    fits = audit["all_whole_files_fit"]
    print(
        json.dumps(
            {
                "status": "pass" if fits else "overrun",
                "scene_count": audit["scene_count"],
                "overrun_segments": [
                    row["id"] for row in audit["segments"] if not row["whole_file_fits"]
                ],
            },
            ensure_ascii=False,
        )
    )
    return 0 if fits else 1


if __name__ == "__main__":
    raise SystemExit(main())
