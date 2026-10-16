from __future__ import annotations

from pathlib import Path
import json
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.font_manager import FontProperties

ROOT = Path(
    "/Users/yanchaomei/Documents/Codex/2026-10-01/daocloud-contextstore-1-kv-cache-nvme/work/ContextStore/kv-service/verification/results/competition-demo-20261016-v2"
)
font = FontProperties(fname="/System/Library/Fonts/STHeiti Medium.ttc")
plt.rcParams.update(
    {
        "font.size": 12,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.spines.left": False,
        "axes.edgecolor": "#CFDBE3",
        "axes.labelcolor": "#172B43",
        "text.color": "#172B43",
        "xtick.color": "#526B80",
        "ytick.color": "#172B43",
        "axes.unicode_minus": False,
    }
)
rows = []
for size in [64, 256]:
    d = json.loads((ROOT / f"assets/perf-{size}.json").read_text())
    c = d["comparisons"]["direct_over_contiguous_throughput"]
    rows.append((size, c["geometric_mean"], *c["block_bootstrap_95pct"], c["blocks_above_one"]))
fig, ax = plt.subplots(figsize=(11, 4.7))
fig.subplots_adjust(left=0.15, right=0.95, top=0.76, bottom=0.23)
for i, (size, mean, lo, hi, n) in enumerate(rows):
    y = 1 - i
    ax.errorbar(
        mean,
        y,
        xerr=[[mean - lo], [hi - mean]],
        fmt="o",
        markersize=12,
        capsize=8,
        color="#126B8A",
        linewidth=3,
    )
    ax.text(hi + 0.012, y, f"{mean:.3f}×  ({n}/10 blocks)", va="center", fontsize=13)
ax.axvline(1, color="#A6B7C5", linestyle="--")
ax.set_yticks([1, 0], ["64 MiB", "256 MiB"])
ax.set_xlim(0.97, 1.36)
ax.set_ylim(-0.55, 1.55)
ax.grid(axis="x", alpha=0.16)
ax.set_xlabel("Direct / packed-contiguous throughput", labelpad=10)
fig.text(
    0.05,
    0.92,
    "固定调度后，直接有序暂存仍有收益",
    fontproperties=font,
    fontsize=21,
    weight="bold",
)
fig.text(
    0.05,
    0.83,
    "2026-10-09 归档实验 · 双 RXE / KVM · 相同条带计划",
    fontproperties=font,
    fontsize=12,
    color="#526B80",
)
fig.text(
    0.05,
    0.075,
    "每个尺寸 10 个独立进程块；横线为 95% 块 bootstrap 区间。",
    fontproperties=font,
    fontsize=11,
)
fig.text(
    0.05,
    0.025,
    "两个尺寸的服务端源条件不同，分别作尺寸内比较；不作为物理双 HCA 聚合结论。",
    fontproperties=font,
    fontsize=10,
    color="#526B80",
)
fig.savefig(ROOT / "assets/direct-staging-evidence.png", dpi=200, facecolor="white")
fig.savefig(ROOT / "assets/direct-staging-evidence.pdf", facecolor="white")
plt.close(fig)
d = json.loads((ROOT / "assets/generation-witness.json").read_text())
r = d["runs"][0]
trans = r["rejection_identity"]["transitions"]
fig, ax = plt.subplots(figsize=(11, 5))
fig.subplots_adjust(left=0.12, right=0.94, top=0.77, bottom=0.27)
values = [
    r["accepted"],
    r["rejection_identity"]["generation_transition_rejections"],
    r["rejected"] - r["rejection_identity"]["generation_transition_rejections"],
]
labels = ["完整内容且身份一致", "发现版本切换后安全拒绝", "传输阶段 Descriptor stale"]
bars = ax.barh([2, 1, 0], values, color=["#12A487", "#E7A63B", "#7596B3"], height=0.56)
ax.set_yticks([2, 1, 0], labels, fontproperties=font)
ax.set_xlim(0, 12)
ax.set_xticks([0, 2, 4, 6, 8, 10])
ax.grid(axis="x", alpha=0.14)
ax.set_axisbelow(True)
for b, v in zip(bars, values):
    ax.text(v + 0.12, b.get_y() + b.get_height() / 2, str(v), va="center", fontsize=17)
fig.text(0.05, 0.92, "版本在读取期间变化，结果可以安全拒绝", fontproperties=font, fontsize=21)
fig.text(
    0.05,
    0.83,
    "2026-10-12 归档见证 · 20 次读取 / 15 次唯一内容覆盖",
    fontproperties=font,
    fontsize=12,
    color="#526B80",
)
fig.text(
    0.05,
    0.15,
    "8 次明确见证：" + "，".join(f"{a}→{b}" for a, b in trans),
    fontproperties=font,
    fontsize=11,
)
fig.text(
    0.05,
    0.07,
    "观察的是同一次读取两次查询间的 Generation 增长；不定位精确服务端提交或在途 WRITE 时刻。",
    fontproperties=font,
    fontsize=10,
    color="#526B80",
)
fig.savefig(
    ROOT / "assets/generation-witness-evidence.png",
    dpi=200,
    bbox_inches="tight",
    facecolor="white",
)
fig.savefig(
    ROOT / "assets/generation-witness-evidence.pdf",
    bbox_inches="tight",
    facecolor="white",
)
plt.close(fig)
print("Four evidence plot files generated from archived audit JSON")
