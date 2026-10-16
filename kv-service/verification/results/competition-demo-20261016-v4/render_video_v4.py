from __future__ import annotations

# Render a narrated walkthrough using unchanged illustrations and real timed output.
import argparse
import json
from pathlib import Path
import re
import subprocess
import tempfile
from PIL import Image, ImageDraw, ImageFont

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--evidence-root", type=Path, required=True)
args = parser.parse_args()
ROOT = args.root
EVIDENCE = args.evidence_root
W, H, FPS = 1920, 1080, 3
BG = "#0B1220"
PANEL = "#142238"
WHITE = "#ECF3FA"
MUTED = "#ABC0D0"
CYAN = "#58D2D7"
AMBER = "#F0BE6A"
CJK = "/System/Library/Fonts/STHeiti Medium.ttc"
MONO = "/System/Library/Fonts/Menlo.ttc"


def f(size, mono=False):
    return ImageFont.truetype(MONO if mono else CJK, size)


def run(cmd):
    r = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if r.returncode:
        raise RuntimeError(r.stderr.decode(errors="replace")[-1200:])
    return r.stdout


scenes = json.loads((ROOT / "video-scenes-v4.json").read_text())["segments"]
events = json.loads((EVIDENCE / "live-events.json").read_text())
read_marker = "ffi_rxe_late_write.py --library baseline.so"
fixed_marker = "ffi_rxe_late_write.py --library candidate.so"
put_marker = "Controlled CQ poll suppression;"
# Split only at boundaries actually printed by the live driver.
read_events = []
late_events = []
put_events = []
full_output = "".join(event[2] for event in events)
late_at = full_output.index("$ /usr/bin/python3 " + read_marker)
put_at = full_output.index("$ CS_CQ_DELAY_LOG=")
offset = 0
for stamp, kind, text in events:
    end = offset + len(text)
    for target, lo, hi in [
        (read_events, 0, late_at),
        (late_events, late_at, put_at),
        (put_events, put_at, len(full_output)),
    ]:
        left = max(offset, lo)
        right = min(end, hi)
        if left < right:
            target.append((stamp, text[left - offset : right - offset]))
    offset = end


def clean(text):
    text = re.sub(
        r"(?m)^\*\* WARNING: connection is not using a post-quantum.*\n\*\* This session may be vulnerable.*\n\*\* The server may need to be upgraded.*\n?",
        "",
        text,
    )
    return re.sub(r"\x1b\[[0-9;]*m", "", text).replace("\r", "")


def wrap(text, width=126):
    lines = []
    for raw in clean(text).splitlines():
        line = raw
        while len(line) > width:
            at = max(line.rfind(" ", 0, width), line.rfind(",", 0, width))
            if at < 40:
                at = width
            lines.append(line[: at + 1])
            line = "    " + line[at + 1 :].lstrip()
        lines.append(line)
    return lines


def playback(data, elapsed, duration):
    if not data:
        return ""
    # Waiting is explicitly shortened; event contents and order remain unchanged.
    mapped = []
    position = 0
    last = data[0][0]
    for stamp, text in data:
        position += min(max(0, stamp - last), 4)
        mapped.append((position, text))
        last = stamp
    scale = max(1, mapped[-1][0])
    progress = min(scale, elapsed / max(1, duration - 10) * scale)
    return "".join(text for t, text in mapped if t <= progress)


def base(scene, total):
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    d.text(
        (70, 30),
        "RailFuse ContextStore  /  2026-10-16 · V4",
        font=f(25, True),
        fill=CYAN,
    )
    d.text((70, 78), scene["title"], font=f(40), fill=WHITE)
    d.text(
        (70, 1018),
        "真实终端记录 + 机制图 + 已注明日期的归档实验  |  完整原始事件流另附",
        font=f(24),
        fill=MUTED,
    )
    d.text(
        (1750, 1018),
        f"{int(total)//60:02d}:{int(total)%60:02d}",
        font=f(25, True),
        fill=CYAN,
    )
    d.rectangle((70, 1062, 1850, 1069), fill="#2A3B50")
    d.rectangle((70, 1062, 70 + int(1780 * total / 270), 1069), fill=CYAN)
    return im, d


def image_scene(scene, total):
    im, d = base(scene, total)
    image = Image.open(ROOT / "assets" / scene["visual"]).convert("RGB")
    image.thumbnail((1780, 825), Image.Resampling.LANCZOS)
    im.paste(image, ((W - image.width) // 2, 150 + (825 - image.height) // 2))
    return im


def terminal_scene(scene, total, elapsed, data, phase_duration):
    im, d = base(scene, total)
    d.rounded_rectangle((65, 150, 1855, 990), radius=18, fill=PANEL)
    d.text(
        (98, 172),
        "REAL CAPTURE  /  EDITED REPLAY"
        + (
            " · 约30秒等待在片中缩短显示"
            if scene["id"] in ["late", "put"]
            else " · 按实际输出顺序展示"
        ),
        font=f(22),
        fill=AMBER,
    )
    text = playback(data, elapsed, phase_duration)
    lines = wrap(text)
    visible = 25
    # Keep a readable final viewport with the witness fields.
    scroll = max(0, len(lines) - visible)
    for j, line in enumerate(lines[scroll : scroll + visible]):
        color = CYAN if line.startswith("$") or line.startswith("LIVE:") else WHITE
        if any(
            token in line
            for token in [
                "corruption: True",
                "intact: False",
                "Waiting for",
                "Controlled CQ",
            ]
        ):
            color = AMBER
        d.text((98, 217 + j * 27), line, font=f(22, True), fill=color)
    if not lines:
        d.text((98, 240), "Starting recorded command...", font=f(26, True), fill=MUTED)
    return im


def intro(scene, total):
    im, d = base(scene, total)
    d.text((100, 240), "一次读取，一个完整对象", font=f(76), fill=WHITE)
    d.text((105, 350), "以更少的内存搬运，管理版本、发布与DMA回收", font=f(37), fill=MUTED)
    for x, title, body in [
        (100, "怎样分配路径", "Placement · 条带计划 · 资源预算"),
        (710, "何时发布结果", "私有暂存 · 完整性 · 版本复查"),
        (1320, "何时回收内存", "QP退休 · CQ排空 · 重连恢复"),
    ]:
        d.rounded_rectangle(
            (x, 520, x + 500, 760), radius=20, fill=PANEL, outline="#2D5771", width=2
        )
        d.text((x + 26, 555), title, font=f(36), fill=CYAN)
        d.text((x + 26, 635), body, font=f(25), fill=WHITE)
    d.text(
        (105, 860),
        "本片包含新采集的真实RXE操作；性能与版本见证分别标明原始日期。",
        font=f(29),
        fill=MUTED,
    )
    return im


def summary(scene, total):
    im, d = base(scene, total)
    entries = [
        ("01", "打开作品介绍", "架构图 → 固定调度对照 → 版本切换 → 生命周期"),
        (
            "02",
            "在已配置客户端执行",
            "python3 run_demo.py read / late / put --output 新目录",
        ),
        ("03", "核对关键结果", "完整内容 · 对象哈希 · 每轨字节 · 哨兵 · 三轮恢复"),
        ("04", "保存并展示证据", "源码版本 / 二进制哈希 / 命令 / exit / JSON / events"),
    ]
    for i, (num, title, body) in enumerate(entries):
        y = 205 + i * 170
        d.rounded_rectangle((90, y, 1830, y + 140), radius=15, fill=PANEL)
        d.text((120, y + 30), num, font=f(44, True), fill=CYAN)
        d.text((225, y + 20), title, font=f(34), fill=WHITE)
        d.text((225, y + 78), body, font=f(27), fill=MUTED)
    d.text(
        (95, 925),
        "github.com/yanchaomei/ContextStore  ·  research/direct-ordered-staging",
        font=f(27, True),
        fill=CYAN,
    )
    return im


qa = []
with tempfile.TemporaryDirectory(prefix="railfuse-video-v4-") as temp:
    temp = Path(temp)
    pcm = bytearray()
    for i, s in enumerate(scenes):
        aiff = ROOT / "narration" / (s["id"] + ".mp3")
        duration = float(
            run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(aiff),
                ]
            )
        )
        if duration > s["duration"] - 0.3:
            raise RuntimeError(f'narration exceeds scene: {s["id"]} {duration}')
        qa.append(
            dict(
                scene=s["id"],
                voice="zh-CN-XiaoxiaoNeural",
                audio_seconds=duration,
                scene_seconds=s["duration"],
                complete_audio_fits=True,
            )
        )
        pcm.extend(
            run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-i",
                    str(aiff),
                    "-af",
                    "apad",
                    "-t",
                    str(s["duration"]),
                    "-f",
                    "s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "1",
                    "-",
                ]
            )
        )
    (temp / "audio.pcm").write_bytes(pcm)
    encoder = subprocess.Popen(
        [
            "ffmpeg",
            "-v",
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
            str(temp / "mute.mp4"),
        ],
        stdin=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    total = 0
    for s in scenes:
        for frame in range(s["duration"] * FPS):
            elapsed = frame / FPS
            insert = s.get("insert")
            if insert and insert["from"] <= elapsed < insert["until"]:
                diagram_scene = dict(s, visual=insert["file"])
                im = image_scene(diagram_scene, total)
            elif s["id"] == "intro":
                im = intro(s, total)
            elif s["id"] == "reproduce":
                im = summary(s, total)
            elif s["id"] == "read":
                im = terminal_scene(s, total, elapsed, read_events, s["duration"])
            elif s["id"] == "late":
                if elapsed < 8:
                    im = image_scene(s, total)
                else:
                    im = terminal_scene(s, total, elapsed - 8, late_events, s["duration"] - 8)
            elif s["id"] == "put":
                im = terminal_scene(s, total, elapsed - 8, put_events, s["duration"] - 8)
            else:
                im = image_scene(s, total)
            if frame == min(6 * FPS, s["duration"] * FPS - 1):
                im.save(ROOT / f'preview-{s["id"]}.png')
            if frame == 0 and s["id"] == "intro":
                im.save(ROOT / "RailFuse-demo-poster-v4.png")
            encoder.stdin.write(im.tobytes())
            total += 1 / FPS
        print("rendered", s["id"], flush=True)
    encoder.stdin.close()
    rc = encoder.wait()
    if rc:
        raise RuntimeError(encoder.stderr.read().decode()[-1200:])
    run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-y",
            "-i",
            str(temp / "mute.mp4"),
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "1",
            "-i",
            str(temp / "audio.pcm"),
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-af",
            "loudnorm=I=-18:TP=-1.5:LRA=11",
            "-movflags",
            "+faststart",
            "-shortest",
            str(ROOT / "RailFuse-demo-v4.mp4"),
        ]
    )
(ROOT / "video-audio-qa.json").write_text(
    json.dumps(
        {
            "voice": "zh-CN-XiaoxiaoNeural",
            "provider": "Microsoft Edge online neural TTS",
            "rate": "-4%; no trimming or time compression",
            "segments": qa,
            "duration_seconds": 270,
            "terminal_capture_duration_seconds": events[-1][0],
            "waiting_gaps_capped_seconds_in_video": 4,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(ROOT / "RailFuse-demo-v4.mp4")
