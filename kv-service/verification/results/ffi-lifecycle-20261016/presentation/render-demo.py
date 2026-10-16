"""Render a narrated, explicitly edited terminal record from live command captures."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parent
CAPTURE = ROOT / 'RailFuse-demo-capture-20261016.json'
VIDEO = ROOT / 'RailFuse-demo-20261016.mp4'
POSTER = ROOT / 'RailFuse-demo-poster-20261016.png'
W, H, FPS = 1280, 720, 3
CHINESE = "/System/Library/Fonts/STHeiti Medium.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"
BG = (11, 18, 31)
PANEL = (21, 31, 46)
WHITE = (232, 240, 248)
MUTED = (154, 175, 194)
CYAN = (95, 221, 229)
AMBER = (255, 205, 112)


def run(*argv: str, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        argv, input=input_bytes, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    if result.returncode:
        raise RuntimeError(
            f"{' '.join(argv)}: {result.stderr[-1000:].decode(errors='replace')}"
        )
    return result.stdout


def terminal_lines(scene: dict) -> list[str]:
    def wrap_line(line: str) -> list[str]:
        wrapped = []
        while len(line) > 112:
            split = max(line.rfind(",", 0, 108), line.rfind(" ", 0, 108))
            if split < 50:
                split = 108
            wrapped.append(line[: split + 1])
            line = "    " + line[split + 1 :].lstrip()
        wrapped.append(line)
        return wrapped

    lines = []
    for command in scene["commands"]:
        status = "EXPECTED FAILURE" if command.get("expected_failure") else "OK"
        lines.append(f"$ {command['label']}  [{status}; exit={command['exit_code']}]")
        output = re.sub(r"\x1b\[[0-9;]*m", "", command["output"])
        output = output.replace("\r", "")
        output = re.sub(
            r"(?m)^\*\* WARNING: connection is not using a post-quantum.*\n"
            r"\*\* This session may be vulnerable.*\n"
            r"\*\* The server may need to be upgraded.*\n?",
            "",
            output,
        )
        raw = [line.strip() for line in output.splitlines() if line.strip()]
        if len(raw) > 17:
            raw = (
                raw[:11]
                + [f"… {len(raw) - 16} lines omitted in edited view …"]
                + raw[-5:]
            )
        for line in raw:
            lines.extend(wrap_line(line))
        lines.append("")
    return lines


def frame(
    scene: dict, number: int, count: int, total_seconds: int, all_lines: list[str]
) -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    draw = ImageDraw.Draw(img)
    title_font = ImageFont.truetype(CHINESE, 35)
    label_font = ImageFont.truetype(MONO, 18)
    body_font = ImageFont.truetype(MONO, 16)
    small_font = ImageFont.truetype(CHINESE, 19)
    draw.text(
        (52, 35), "RailFuse ContextStore  /  Recorded on 2026-10-16", font=label_font, fill=CYAN
    )
    draw.text((52, 80), scene["title"], font=title_font, fill=WHITE)
    draw.rounded_rectangle((51, 141, 1229, 635), radius=14, fill=PANEL)
    draw.text(
        (76, 164),
        "REAL VERBS RECEIPTS  /  EDITED NARRATED VIEW",
        font=label_font,
        fill=AMBER,
    )
    elapsed = number / FPS
    # Let the visible transcript scroll after the first few seconds, preserving command order.
    visible = 20
    max_start = max(0, len(all_lines) - visible)
    start = min(max_start, int(max_start * min(1.0, elapsed / max(1, count / FPS - 5))))
    for index, line in enumerate(all_lines[start : start + visible]):
        color = CYAN if line.startswith("$") else WHITE
        if (
            "EXPECTED FAILURE" in line
            or "error" in line.lower()
            or "failed" in line.lower()
        ):
            color = AMBER
        # A terminal transcript can be wide; clip at the panel edge without changing its contents.
        draw.text((76, 202 + index * 20), line[:120], font=body_font, fill=color)
    minute, second = divmod(total_seconds, 60)
    draw.text(
        (52, 657),
        "SOFT-ROCE / KVM  ·  2 independent RXE paths  ·  not HCA bandwidth evidence",
        font=small_font,
        fill=MUTED,
    )
    draw.text((1118, 657), f"{minute:02d}:{second:02d}", font=label_font, fill=CYAN)
    draw.rectangle((52, 696, 1228, 701), fill=(42, 59, 75))
    draw.rectangle((52, 696, 52 + int(1176 * total_seconds / 240), 701), fill=CYAN)
    return img


def main() -> None:
    capture = json.loads(CAPTURE.read_text())
    scenes = capture["scenes"]
    assert sum(s["duration_seconds"] for s in scenes) == 240
    assert all(s["commands"] for s in scenes)
    assert all(
        command["exit_code"] == 0 or command.get("expected_failure")
        for scene in scenes
        for command in scene["commands"]
    )
    with tempfile.TemporaryDirectory(prefix="cs-rail-demo-") as tmp_dir:
        tmp = Path(tmp_dir)
        audio = bytearray()
        for index, scene in enumerate(scenes):
            aiff = tmp / f"scene-{index}.aiff"
            run(
                "say",
                "-v",
                "Tingting",
                "-r",
                "135",
                "-o",
                str(aiff),
                scene["narration"],
            )
            pcm = run(
                "ffmpeg",
                "-loglevel",
                "error",
                "-i",
                str(aiff),
                "-af",
                "apad",
                "-t",
                str(scene["duration_seconds"]),
                "-f",
                "s16le",
                "-ar",
                "48000",
                "-ac",
                "1",
                "-",
            )
            audio.extend(pcm)
        pcm_path = tmp / "narration.pcm"
        pcm_path.write_bytes(audio)

        mute_video = tmp / "video.mp4"
        encoder = subprocess.Popen(
            [
                "ffmpeg",
                "-loglevel",
                "error",
                "-y",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-s",
                f"{W}x{H}",
                "-r",
                str(FPS),
                "-i",
                "-",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "21",
                "-pix_fmt",
                "yuv420p",
                str(mute_video),
            ],
            stdin=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        assert encoder.stdin is not None
        total_frames = 0
        for scene_index, scene in enumerate(scenes):
            lines = terminal_lines(scene)
            frame_count = scene["duration_seconds"] * FPS
            for scene_frame in range(frame_count):
                total_seconds = total_frames // FPS
                img = frame(scene, scene_frame, frame_count, total_seconds, lines)
                if scene_index == 0 and scene_frame == 0:
                    img.save(POSTER)
                encoder.stdin.write(img.tobytes())
                total_frames += 1
            print(
                f"rendered {scene_index + 1}/{len(scenes)}: {scene['title']}",
                flush=True,
            )
        encoder.stdin.close()
        if encoder.wait() != 0:
            raise RuntimeError(encoder.stderr.read().decode(errors="replace")[-1000:])

        run(
            "ffmpeg",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(mute_video),
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-i",
            str(pcm_path),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-shortest",
            str(VIDEO),
        )
    print(VIDEO)


if __name__ == "__main__":
    main()
