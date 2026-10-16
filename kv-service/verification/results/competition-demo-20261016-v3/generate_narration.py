from __future__ import annotations

import asyncio
import hashlib
import importlib.metadata
import json
from pathlib import Path
import subprocess
import edge_tts

import argparse

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--evidence-root", type=Path, required=True)
args = parser.parse_args()
OUT, ROOT = args.root, args.evidence_root
scene_path = OUT / "video-scenes-v3.json"
scenes = json.loads((scene_path if scene_path.exists() else ROOT / "video-scenes.json").read_text())
texts = {
    "intro": "大家好，这是 Rail Fuse Context Store。我们关注的是：怎样让多条网络路径共同完成一次对象读取，同时管好版本、结果发布和内存回收。下面先看整体机制，再用真实运行记录展示它在正常和异常情况下的表现。",
    "architecture": "这张图分成上下两部分。上方是 Rust 多轨读取：各条路径写入私有暂存，检查全部通过后，才把完整结果交给调用方。直接有序暂存需要满足一定条件。下方是单独的 Python 接口：它先结束失败传输、排空旧完成事件，再允许复用和重新连接。",
    "read": "现在看真实操作。我先写入一个六十四兆的测试对象，再把它完整读回来，逐字节核对。接着，分别用一条和两条路径读取同一个对象。请注意三个结果：内容哈希相同，两条路径各传输三十二兆，结束时，在途请求、在途字节和预留注册预算都回到零。",
    "performance": "功能跑通以后，还要回答：这个优化值不值得？这里是十月九日的归档实验。两种实现采用相同的条带分配，每个尺寸运行十个独立进程块。直接有序暂存的吞吐分别提高大约百分之十和百分之十六。图中保留了区间和获益块数，两个尺寸分别作对照。",
    "version": "另一个问题是：读取过程中，对象更新了怎么办？在十月十二日的归档实验中，二十次读取与十五次内容覆盖并发执行。十次返回完整内容，十次安全拒绝。其中八次，明确看到了同一次读取的版本号发生增长，调用方原来的缓冲区没有被改写。",
    "late": "接下来，比较晚到写入。旧库已经报错返回，但队列对仍然存活，晚到的数据把复用哨兵覆盖成了服务端内容，哈希完全一致。修复版先结束这次传输，保留原来的注册内存，等服务端进入终态后再检查。哨兵保持完整，同一个句柄重连后的写入和读取也通过了内容核对。",
    "put": "最后，看旧完成事件会不会影响下一次请求。测试先受控隐藏完成队列的轮询结果，随后读取底层实际产生的完成事件。旧事件在错误返回前已经排空。接着，同一句柄、同一块注册区域，连续三轮写入和读取都核对了完整内容。",
    "reproduce": "整个演示回答了三个问题：路径怎样协作，安全暂存的开销怎样降低，版本变化和异步错误时怎样守住边界。仓库首页已经放好详细技术说明、操作提词卡、演示脚本和原始记录。按相同的输入与命令，就可以复查这次演示。",
}
for s in scenes["segments"]:
    s["narration"] = texts[s["id"]]
scenes["voice_provider"] = "Microsoft Edge online neural TTS"
scenes["voice"] = "zh-CN-XiaoxiaoNeural"
scenes["voice_client_version"] = importlib.metadata.version("edge-tts")
scenes["speech_rate"] = "-4%"
scenes["rate_policy"] = (
    "Keep natural rate; refuse segments that exceed scene duration, never truncate."
)
(OUT / "video-scenes-v3.json").write_text(json.dumps(scenes, ensure_ascii=False, indent=2) + "\n")
A = OUT / "narration"
A.mkdir(parents=True, exist_ok=True)


async def synth(s, sem):
    async with sem:
        p = A / (s["id"] + ".mp3")
        meta = A / (s["id"] + ".boundaries.jsonl")
        for attempt in range(3):
            try:
                await edge_tts.Communicate(
                    s["narration"],
                    voice="zh-CN-XiaoxiaoNeural",
                    rate="-4%",
                    pitch="+0Hz",
                    receive_timeout=90,
                ).save(str(p), str(meta))
                break
            except Exception as exc:
                if attempt == 2:
                    raise RuntimeError(f'{s["id"]}: {type(exc).__name__}') from None
                await asyncio.sleep(2)
        duration = float(
            subprocess.check_output(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_entries",
                    "format=duration",
                    "-of",
                    "default=nw=1:nk=1",
                    str(p),
                ]
            )
        )
        row = dict(
            scene=s["id"],
            audio_duration=duration,
            scene_duration=s["duration"],
            complete_audio_fits=duration < s["duration"] - 0.3,
            voice="zh-CN-XiaoxiaoNeural",
            rate="-4%",
            sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
            text_sha256=hashlib.sha256(s["narration"].encode()).hexdigest(),
        )
        print(json.dumps(row, ensure_ascii=False), flush=True)
        return row


async def main():
    sem = asyncio.Semaphore(2)
    rows = await asyncio.gather(*[synth(s, sem) for s in scenes["segments"]])
    (OUT / "narration-qa.json").write_text(
        json.dumps(
            {
                "voice": "zh-CN-XiaoxiaoNeural",
                "client_version": importlib.metadata.version("edge-tts"),
                "segments": rows,
                "all_fit": all(x["complete_audio_fits"] for x in rows),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n"
    )


asyncio.run(main())
