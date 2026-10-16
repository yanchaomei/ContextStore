from __future__ import annotations

# Build competition artifacts from immutable audit inputs and live receipts.
import argparse
from PIL import Image as PILImage
import json
from pathlib import Path
from xml.sax.saxutils import escape
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    PageBreak,
    Table,
    TableStyle,
    Image,
)

parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
args = parser.parse_args()
ROOT = args.root
A = ROOT / "assets"
BASE = "https://github.com/yanchaomei/ContextStore"
BRANCH = BASE + "/tree/research/direct-ordered-staging"
PUBLIC = (
    BASE
    + "/raw/refs/heads/research/direct-ordered-staging/kv-service/verification/results/competition-demo-20261016-v3/"
)
CORE = "39ce2d50fcee00405758f1e2c4f7bd389baccf1c"
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
INK = colors.HexColor("#172B43")
BLUE = colors.HexColor("#126B8A")
GRAY = colors.HexColor("#566E82")
PALE = colors.HexColor("#EEF6F8")
RULE = colors.HexColor("#CCDCE5")
TEAL = colors.HexColor("#12A487")


def style(name, size, leading, **kw):
    options = dict(
        fontName="STSong-Light",
        fontSize=size,
        leading=leading,
        textColor=INK,
        spaceAfter=9,
        wordWrap="CJK",
    )
    options.update(kw)
    return ParagraphStyle(name, **options)


TITLE = style("title", 27, 36)
SUB = style("sub", 12.5, 21, textColor=GRAY)
H1 = style("h1", 17, 25, textColor=BLUE, spaceAfter=14)
H2 = style("h2", 12.5, 20, textColor=BLUE, spaceBefore=5)
B = style("body", 10.8, 18)
SM = style("small", 8.7, 14, textColor=GRAY)
C = style("cell", 9.1, 14.5, spaceAfter=0)
CH = style("head", 9.5, 15, textColor=colors.white, spaceAfter=0)
CODE = style("code", 8.4, 13.5, fontName="Courier", textColor=BLUE)


def p(text, s=B):
    return Paragraph(text, s)


def link(text, url):
    return '<link color="#126B8A" href="' + url + '"><u>' + text + "</u></link>"


def grid(rows, widths):
    t = Table(
        [[p(cell, CH if i == 0 else C) for cell in row] for i, row in enumerate(rows)],
        colWidths=widths,
        repeatRows=1,
        hAlign="LEFT",
    )
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.35, RULE),
            ]
        )
    )
    return t


WIDTH = A4[0] - 40 * mm


def pic(name, width=WIDTH):
    path = A / name
    with PILImage.open(path) as im:
        iw, ih = im.size
    return Image(str(path), width=width, height=width * ih / iw)


def decor(c, doc):
    c.saveState()
    w, h = A4
    c.setStrokeColor(RULE)
    c.line(20 * mm, h - 22 * mm, w - 20 * mm, h - 22 * mm)
    c.setFont("STSong-Light", 8)
    c.setFillColor(GRAY)
    c.drawString(20 * mm, h - 18 * mm, "RailFuse ContextStore  |  智算云赛道  |  2026-10-16 / V3")
    c.drawString(20 * mm, 16 * mm, "源码、实验日期、视频场景与原始收据分别可追溯")
    c.drawRightString(w - 20 * mm, 16 * mm, str(doc.page))
    c.restoreState()


def build(name, story, title):
    SimpleDocTemplate(
        str(ROOT / name),
        pagesize=A4,
        leftMargin=20 * mm,
        rightMargin=20 * mm,
        topMargin=29 * mm,
        bottomMargin=27 * mm,
        title=title,
        author="RailFuse ContextStore",
    ).build(story, onFirstPage=decor, onLaterPages=decor)


segments = json.loads((ROOT / "video-scenes-v3.json").read_text())["segments"]
guide = []


def gp(title):
    if guide:
        guide.append(PageBreak())
    guide.append(p(title, H1))


gp("演示提词卡 1 / 3：准备与完整顺序")
guide += [
    p("录制样片：4分30秒。本人现场语速与切换需排练，建议另留30秒余量。"),
    p("开场一句话", H2),
    p("“RailFuse 围绕一次 KV 对象读取，管理怎样分配路径、何时发布结果、何时回收内存。”"),
    p("先打开的三个窗口", H2),
    p(
        "1. 仓库首页 / 作品介绍 PDF。<br/>2. 已配置的隔离 RXE 客户端终端，进入 demo 文件目录。<br/>3. 本演示提词卡；故障时可直接展示真实归档收据。"
    ),
    p("环境预检", H2),
    p(
        "确认服务端进程存在、两条 RXE 链路 ACTIVE、设备 / GID / 地址与配置一致。原有库、服务端和演示脚本的哈希应与 launch.json 对应。旧库仅在隔离负对照中加载。"
    ),
    grid(
        [
            ["视频时间", "画面 / 动作", "你要表达的意思"],
            ["00:00-00:25", "作品标题与三项决策", "解决对象级交付问题"],
            ["00:25-00:55", "总体机制图", "Rust 多轨与 Python FFI 分开讲"],
            ["00:55-01:35", "read 真实终端", "完整内容、哈希和每轨字节"],
            ["01:35-02:10", "固定调度性能对照", "10月9日历史实测与比较口径"],
            ["02:10-02:45", "版本切换见证", "10月12日：10成功、8版本拒绝、2 stale"],
            ["02:45-03:25", "late 对照与终端", "等终态后检查同一注册内存"],
            ["03:25-04:00", "put 终端", "真实旧WC、同句柄同MR、三轮恢复"],
            ["04:00-04:30", "仓库入口", "复现条件、证据与后续边界"],
        ],
        [92, 175, WIDTH - 267],
    ),
]
gp("演示提词卡 2 / 3：三条命令看懂关键结果")
guide += [
    p(
        "以下命令在已配置的 RXE 客户端、演示目录中执行。每次用新的输出目录编号；输出目录已存在会拒绝重用。完整环境准备见目录 README。",
        SM,
    )
]
for mode, title, command, expect, saytext in [
    (
        "read",
        "第一步：真实读写与双轨分工",
        "python3 run_demo.py read --output receipts-read-01",
        "full_content_verified: True；单轨与双轨 xxh3=a0a4cbfa5cad46af；双轨 rail_bytes=[33554432,33554432]；direct=1；在途请求、字节和预留注册预算归零。",
        "“我先写入并核对完整内容，再改变网络路径数量。看相同对象哈希与每条轨道的字节，确认两条路径共同完成同一对象。”",
    ),
    (
        "late",
        "第二步：晚到写入负对照和修复",
        "python3 run_demo.py late --output receipts-late-01",
        "旧库污染 True、哨兵完整 False；修复版哨兵完整 True、污染 False、同句柄重连内容校验 True。修复版通常等待约30秒服务端终态。",
        "“错误返回不等于 DMA 已经结束。我保留同一块注册内存和 rkey，直到晚到请求终止才检查，再用同一句柄验证恢复。”",
    ),
    (
        "put",
        "第三步：旧完成事件与三轮恢复",
        "python3 run_demo.py put --output receipts-put-01",
        "旧真实WC已在错误前排空 True；同注册区域复用 True；reconnect_put_get_full_content_rounds: 3；passed: True。通常约31秒。",
        "“这个测试受控隐藏轮询结果，但记录的完成事件来自真实 provider。这里看旧事件排空和三轮完整内容，不要求重连后 QP 编号不同。”",
    ),
]:
    guide += [
        p(title, H2),
        p(escape(command), CODE),
        p("看这些输出：" + expect),
        p(saytext, SM),
        Spacer(1, 7),
    ]
guide += [
    p("一次运行全部步骤：", H2),
    p("python3 run_demo.py all --output receipts-all-01", CODE),
    p(
        "样片保留了本轮约66秒完整终端事件流。四分半视频额外包含机制讲解、历史性能和版本见证，剪辑口径已标注。",
        SM,
    ),
]
gp("演示提词卡 3 / 3：切换、追问与失败处理")
guide += [
    p("讲性能：只读主对照", H2),
    p(
        "“Direct 对同调度 packed-contiguous，64 MiB 为1.100倍，256 MiB 为1.161倍。每个尺寸十个进程块；这是软件 RXE 场景，两个尺寸分别比较。”<br/>不要把单次演示的耗时当统计性能优势。"
    ),
    p("讲版本：只说实际观察到什么", H2),
    p(
        "“8次读取在两次查询间看到了 Generation 增长，均拒绝且保留调用方原值。这个见证没有定位精确服务端提交和在途 WRITE 的重叠时刻。”"
    ),
    p("问：是不是把两条连接拼起来？", H2),
    p(
        "“路径并行只是执行手段。我们还约束对象身份、私有暂存区域、资源预算、发布门，以及失败后 DMA 结束和缓冲复用的条件；对应有不同的反例和对照。”"
    ),
    p("问：Python 也具有多轨原子发布吗？", H2),
    p(
        "“现有 Python FFI 是单连接外部缓冲区接口。它有失败前退休和重连安全，失败GET可能留下部分内容；Rust RailReader 的原子发布另行实现与验证。”"
    ),
    p("现场失败怎么办", H2),
    p(
        "连接失败时先停在错误输出，核对服务端、RXE链路和GID；不把空结果当成功。基线没有精确污染、修复版哨兵改变、重连内容不一致或WC判据不满足，都应保留失败并停止该演示。展示随包保存的真实成功记录时，明确说明正在回放归档。"
    ),
    p("录屏建议", H2),
    p(
        "先打开PDF第1章总体机制、第18章性能与第19章版本见证，以及终端，终端字号保持可读。每条命令执行前说“我要验证什么”，结果出来后只指出2到3个关键字段；等待时解释机制。结尾回到仓库首页，展示PDF、视频、提词卡和原始收据入口。"
    ),
]
build("RailFuse-demo-guide-v3.pdf", guide, "RailFuse ContextStore - Live Demo Guide V3")
text = []
start = 0
for s in segments:
    end = start + s["duration"]
    text.append(
        f"{start//60:02d}:{start%60:02d}-{end//60:02d}:{end%60:02d}  {s['title']}\n画面/操作：{s['action']}\n看什么：{s['observe']}\n讲稿：{s['narration']}\n"
    )
    start = end
(ROOT / "演示逐步操作与完整讲稿.txt").write_text(
    "\n".join(text) + "\n现场完整命令：python3 run_demo.py all --output receipts-all-新编号\n"
)
print("Built V3 guide and matching narration script")
