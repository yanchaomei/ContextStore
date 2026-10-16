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
L = ROOT / "live/receipts-take1"
for name in ["completed", "fixed", "put-cq"]:
    d = json.loads((L / (name + ".json")).read_text())
    assert d.get("passed", d.get("same_handle_reconnect_put_get_full_content")) is True
BASE = "https://github.com/yanchaomei/ContextStore"
BRANCH = BASE + "/tree/research/direct-ordered-staging"
PUBLIC = (
    BASE
    + "/raw/refs/heads/research/direct-ordered-staging/kv-service/verification/results/competition-demo-20261016-v2/"
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
    c.drawString(20 * mm, h - 18 * mm, "RailFuse ContextStore  |  智算云赛道  |  2026-10-16 / V2")
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


story = []


def page(title):
    if story:
        story.append(PageBreak())
    story.append(p(title, H1))


page("RailFuse ContextStore")
story += [
    Spacer(1, 18),
    p("以更少的内存搬运，<br/>交付可验证的完整 KV 对象", TITLE),
    p("面向版本化 KV Cache 对象的故障原子多轨读取引擎", SUB),
    Spacer(1, 15),
    p(
        "单个推理 Worker 使用多条 RDMA 路径读取既有条带对象，在私有暂存中完成既定完整性与身份检查后统一发布；异步错误发生后，先结束相关 DMA 所有权，再允许内存复用。"
    ),
    Spacer(1, 10),
    grid(
        [
            ["怎样分配路径", "何时发布结果", "何时回收内存"],
            [
                "Placement 与版本约束<br/>按字节规划和资源预算",
                "私有有序暂存<br/>完整性、版本和取消检查",
                "QP 退休与 CQ 排空<br/>显式重连和句柄生命周期",
            ],
        ],
        [WIDTH / 3] * 3,
    ),
    Spacer(1, 18),
    p("三组互补证据", H2),
    p(
        "开销：固定调度的直接暂存对照。<br/>版本：读取期间的 Generation 切换见证。<br/>生命周期：旧库真实污染负对照与修复版复用、重连验证。"
    ),
    Spacer(1, 12),
    p(
        "作品基于 DaoCloud ContextStore；沿用其服务、协议和磁盘布局，新增客户端路径规划、私有暂存与发布控制，并强化跨语言接口生命周期。"
    ),
    p("核心安全源码：" + CORE, SM),
    p(link("提交仓库与最新材料入口", BRANCH), SM),
]
page("01  一次对象读取，三个不同的完成条件")
story += [
    p(
        "在共享 KV Cache 场景中，推理 Worker 从存储读取一个对象。对象由多个条带组成，读取期间可能被覆盖更新；一条 RDMA 路径可能失败，晚到写入也可能越过 API 的错误返回。"
    ),
    grid(
        [
            ["失败原因", "为什么简单并行不够", "对应设计"],
            [
                "对象与路径的身份约束",
                "字节相加完整，也可能混入已经失效的对象身份",
                "复用 Placement 与条带归属，传输后复查版本和布局",
            ],
            [
                "隔离部分结果的开销",
                "各轨 packed 缓冲区再组装，会增加完整对象的内存搬运",
                "符合条件时，各轨直接写入同一私有有序暂存的不同区域",
            ],
            [
                "请求与 DMA 的结束时刻不同",
                "错误已返回，QP 或旧完成事件仍可能影响后续复用",
                "返回前退休 QP、排空 CQ，建立明确的重连和释放边界",
            ],
        ],
        [108, 187, WIDTH - 295],
    ),
    Spacer(1, 15),
    p("统一管理的是“这次对象读取的交付条件”", H2),
    p(
        "路径计划规定读取哪些字节；发布门规定调用方何时能接受结果；退休与回收规定失败后哪些资源可以重新使用。三个决策形成一个可解释、可验证的对象读取流程。"
    ),
    p("应用价值与测量范围", H2),
    p(
        "已有证据覆盖真实 Verbs 执行、软件数据路径开销和异常处理。实际业务中的首 token 时延、端到端推理吞吐与成本，仍需结合推理框架、访问局部性和部署硬件测量。"
    ),
]
page("02  总体架构：沿用条带，明确发布与回收")
story += [
    pic("mechanism-overview.png"),
    Spacer(1, 12),
    p("Rust 多轨读取：可选直接有序暂存", H2),
    p(
        "根据 Placement、监听端点与本地设备规划条带。直接路径在单存储归属等条件满足时启用，各 Rail 借用互不重叠的私有有序区域；否则回退 packed 路径。所有必要任务结束并通过检查后，再向调用方发布。"
    ),
    p("Python / C ABI：单连接生命周期", H2),
    p(
        "Python 外部缓冲区接口单独采用串行生命周期、错误前退休 QP 和重连前排空 CQ。失败 GET 可以留下已经写入的部分数据；它不自动获得 Rust 多轨读取的原子发布语义。"
    ),
    p(
        "图为机制示意。两条接口的发布语义分别实现与验证。实验图表来自原始审计数据，运行画面来自实际执行记录。",
        SM,
    ),
]
page("03  机制与收益：降低安全暂存的组装开销")
story += [
    p("反例：安全地隔离部分结果，却为完整对象多做一次组装。", H2),
    p(
        "直接有序暂存把符合条件的条带分配成连续区域，让各轨写入一个请求私有向量的互不重叠部分。发布前的校验和、身份复查及取消门保留；避免 packed 到对象顺序的组装步骤。"
    ),
    pic("direct-staging-evidence.png"),
    Spacer(1, 7),
    grid(
        [
            ["对象 / 模式", "客户端 CPU ms / 完成读*", "客户端峰值 RSS MiB"],
            ["64 MiB，packed-contiguous → Direct", "123.9 → 76.5", "206.3 → 142.5"],
            ["256 MiB，packed-contiguous → Direct", "433.6 → 259.4", "782.5 → 526.6"],
        ],
        [205, 150, WIDTH - 355],
    ),
    Spacer(1, 8),
    p(
        "比较固定相同的条带计划；每个尺寸 10 个独立进程块，每模式 1 次预热与 5 次测量。吞吐比按块内延迟中位数形成配对比值；CPU* 含预热和进程设置的摊销，和延迟统计口径不同。",
        SM,
    ),
    p(
        "本页为 2026-10-09 归档实验。两档尺寸使用不同服务端源条件，分别作尺寸内比较；相同注册 / 在途载荷规模下，CPU 与 RSS 下降。净实现效果包含分配、注册与清理差异，不是纯 memcpy 耗时估计。[E1]",
        SM,
    ),
]
page("04  版本见证：读取期间变化就安全拒绝")
story += [
    p("反例：传输开始时有效的对象，在发布之前已被覆盖。", H2),
    p(
        "在“初次查询 → 私有暂存传输 → 再次查询”的顺序中，比较对象、Generation、布局及 Placement 身份。身份变化时拒绝发布，调用方缓冲区保持原值。"
    ),
    pic("generation-witness-evidence.png"),
    Spacer(1, 10),
    p("证据如何解释", H2),
    p(
        "2026-10-12 的归档实验使用一个顺序读客户端和一个覆盖写客户端。20 次读取中，10 次返回完整且身份一致的内容，10 次安全拒绝；其中 8 次明确观察到同一次读取两次查询间的 Generation 增长，另 2 次在传输阶段发现描述符失效。"
    ),
    p(
        "这 8 次见证支持元数据版本切换发生在两次观察之间；不定位精确服务端提交时间，也不证明提交与 RDMA WRITE 在途时刻相交。源码快照、二进制与独立审计均保存。[E2]",
        SM,
    ),
]
page("05  真实反例：错误返回后，内存仍会被写入")
story += [
    pic("late-write-comparison.png"),
    Spacer(1, 13),
    p("本次重新执行的演示结果", H2),
    grid(
        [
            ["观察项", "旧库负对照", "修复版"],
            ["同一注册内存复用后的内容", "精确变成服务端 payload", "0x5A 哨兵保持完整"],
            [
                "内容哈希核对",
                "实际污染哈希 = 预期 payload 哈希",
                "实际哨兵哈希 = 预期哨兵哈希",
            ],
            [
                "错误后同一 native handle 恢复",
                "只记录污染，不作恢复主张",
                "显式重连后 PUT / GET 全内容一致",
            ],
        ],
        [135, 165, WIDTH - 300],
    ),
    Spacer(1, 12),
    p("不可提前省略的观察顺序", H2),
    p(
        "错误返回 → 写入复用哨兵 → 转发延迟请求 → 服务端响应 / EOF → 检查内容 → 注销或进入恢复。原分配和 rkey 保留到检查结束，避免用换缓冲区或提前检查制造假阳性。"
    ),
    p(
        "2026-10-16 V2 新采集：1 MiB、单条 RXE 路径的旧 / 新库演示。更完整的两条链路、1 / 8 MiB、错误标签 / 超时矩阵另存于 10 月 16 日冻结收据。[E3][E4]",
        SM,
    ),
]
page("06  工程闭环：旧事件不进入下一次请求")
story += [
    p("PUT 的另一条风险：旧 CQ 完成事件被下一次请求误用。", H2),
    p(
        "受控观察器暂时令 CQ 轮询返回 0；QP 退休后读取真实 provider WC，验证旧完成事件在错误返回前被消费。随后同一句柄、同一注册内存重连，完成三轮实际 PUT / GET，并逐字节校验。"
    ),
    grid(
        [
            ["证据层", "冻结验收结果", "本次 V2 演示"],
            [
                "Python / 原生生命周期",
                "165 passed、11 skipped；native 13 / 13；receipt 6 / 6",
                "沿用冻结的回归证据，核心实现未改",
            ],
            [
                "真实 RXE GET",
                "修复版 8 / 8；旧库精确污染 2 / 2",
                "重新执行旧 / 新库各 1 次，结果符合判据",
            ],
            [
                "真实 RXE PUT / CQ",
                "两条链路各 1 次；每条三轮恢复",
                "单条链路重录，真实旧 WC 排空与三轮恢复通过",
            ],
            [
                "当前 Rust 使用流程",
                "64 MiB 完整读与每 Rail 字节守恒",
                "重新写入并核对内容，单 / 双轨哈希一致",
            ],
        ],
        [105, 195, WIDTH - 300],
    ),
    Spacer(1, 15),
    p("独立复核发现并修正的两个边界", H2),
    p(
        "描述符与字符串：拒绝 ctypes 整数收窄和 NUL 别名；字段只读取一次，验证并传递同一快照。<br/>关闭与中断：原生 free 前先从 Python 对象移走句柄，避免释放完成后遇到中断而重复释放。"
    ),
    p(
        "V2 保留带时间的终端事件流、每个探针 stdout / exit / JSON / events，以及执行前后库和脚本哈希。CQ 是受控轮询抑制；WC 内容来自真实 provider。[E3][E4]",
        SM,
    ),
]
page("07  应用、部署与演示入口")
story += [
    grid(
        [
            ["适用场景", "可使用能力", "需要结合场景测量"],
            [
                "共享 KV Cache / 长上下文复用",
                "版本化对象，多路径读取与完整发布",
                "推理框架接入、命中模式和端到端收益",
            ],
            [
                "暴露多条网络路径的存储节点",
                "已有布局上的路由匹配和按字节规划",
                "物理双 HCA、多 NVMe 与瓶颈归因",
            ],
            [
                "Python / pinned buffer 调用方",
                "注册、GET / PUT、错误后重连和生命周期边界",
                "原始视图、外部内存的调用方责任",
            ],
        ],
        [126, 181, WIDTH - 307],
    ),
    Spacer(1, 13),
    p("演示时按四个动作推进", H2),
    p(
        "1. 展示架构图，用一句话解释三项决策。<br/>2. 执行 read：写入 64 MiB，再比较单轨与双轨的对象哈希、条带分配和每轨字节。<br/>3. 执行 late：观察旧库污染和修复版哨兵，再展示同一句柄重连结果。<br/>4. 执行 put：指出旧 WC 在错误返回前排空，并核对三轮恢复。"
    ),
    p("配套文件", H2),
    p(
        link("四分半有声演示", PUBLIC + "RailFuse-demo-v2.mp4")
        + "<br/>"
        + link("照着操作与讲解的演示提词卡", PUBLIC + "RailFuse-demo-guide-v2.pdf")
        + "<br/>"
        + link(
            "运行脚本与原始终端记录",
            BRANCH + "/kv-service/verification/results/competition-demo-20261016-v2",
        )
    ),
    p(
        "环境前提：Linux、libibverbs/provider、Rust 与 Python（项目要求 3.10+），服务端 Redis、存储目录和 RXE listener 已配置。项目构建入口为 make build；详细操作见提词卡。",
        SM,
    ),
]
page("08  复现、贡献归属与交付范围")
story += [
    p("完整源码与开源归属", H2),
    p(
        link("RailFuse 提交仓库", BRANCH)
        + "<br/>"
        + link("本轮核心安全修复固定提交", BASE + "/commit/" + CORE)
    ),
    p(
        "作品基于 Apache-2.0 的 DaoCloud ContextStore，沿用其服务、协议、存储条带、元数据和依赖能力。新增客户端机制与研究分支改动按代码历史归属；独立 PR 的审阅、官方 CI 和合并状态单独报告。"
    ),
    p("证据索引", H2),
    p(
        "[E1] 2026-10-09 固定调度开销对照："
        + link(
            "实验说明与原始审计",
            BASE
            + "/blob/3997eabdb241213eddbd668f6902fa7610de52ae/docs/paper/direct-staging-eval-2026-10-09.md",
        ),
        SM,
    ),
    p(
        "[E2] 2026-10-12 Generation 切换："
        + link(
            "见证说明与收据",
            BASE
            + "/blob/3997eabdb241213eddbd668f6902fa7610de52ae/docs/paper/generation-transition-witness-2026-10-12.md",
        ),
        SM,
    ),
    p(
        "[E3] 2026-10-16 冻结 FFI / RXE 验收："
        + link(
            "独立复核、完整矩阵与归因",
            BASE + "/tree/" + CORE + "/kv-service/verification/results/ffi-lifecycle-20261016",
        ),
        SM,
    ),
    p(
        "[E4] 2026-10-16 V2 新终端录制："
        + link(
            "带时间的事件流与逐项原始输出",
            BRANCH + "/kv-service/verification/results/competition-demo-20261016-v2",
        ),
        SM,
    ),
    Spacer(1, 13),
    p("范围与下一项实证", H2),
    p(
        "本次数据面是同一宿主机上的两台 KVM、两条 RXE 网络和虚拟盘。支持真实 Verbs 执行、所测异常场景与软件开销对照；物理双 HCA 聚合、真实 provider 故障、所有交错的形式化证明及端到端推理收益仍需专项验证。"
    ),
    p("画面与时间", H2),
    p(
        "机制图由图像生成工具制作并校对；数据图从真实审计 JSON 绘制。视频按“新终端录制 + 机制图 + 已注明日期的历史证据”剪辑配音；完整未剪辑终端事件流另附。现场本人讲解时长需排练确认。"
    ),
    p(
        "材料准备、仓库推送、正式邮件发送和组委会回执分别留存。按用户提供的通知，作品材料截止 2026-10-16 24:00；收件邮箱 oscc@oschina.cn。",
        SM,
    ),
]
build(
    "RailFuse-project-introduction-v2.pdf",
    story,
    "RailFuse ContextStore - Project Introduction V2",
)

segments = [
    {
        "id": "intro",
        "title": "一次对象读取，三个交付决策",
        "duration": 25,
        "visual": "mechanism-overview.png",
        "kind": "image",
        "narration": "大家好，这是 RailFuse ContextStore。它面向共享 KV Cache 的对象读取：单个 Worker 使用多条 RDMA 路径，同时管理怎样分配路径、何时发布结果和何时回收内存。接下来我用真实运行记录展示完整流程。",
        "action": "打开作品介绍第1页，指向三项决策；随后切到总体机制图。",
        "observe": "先让评委记住对象级交付目标。",
    },
    {
        "id": "architecture",
        "title": "先规划，再检查发布，失败后安全回收",
        "duration": 30,
        "visual": "mechanism-overview.png",
        "kind": "image",
        "narration": "上方是 Rust 多轨读取。条带布局保留，各轨写入私有暂存，全部检查通过后才发布。直接有序暂存是有条件的可选路径。下方是单独的 Python 接口生命周期：错误返回前退休 QP、排空旧完成事件，再允许复用和显式重连。",
        "action": "展示作品介绍第3页。沿左到右箭头讲上方，再指出下方是另一条接口。",
        "observe": "区分 Rust 多轨原子发布与 Python 单连接 FFI。",
    },
    {
        "id": "read",
        "title": "真实操作：写入64 MiB，单轨与双轨读取",
        "duration": 40,
        "kind": "terminal",
        "narration": "在已经配置好的 RXE 客户端，我执行 read。先通过 FFI 写入六十四 MiB，并取回逐字节核对。再由当前 Rust 客户端单轨和双轨读取同一对象。这里看三项：对象哈希相同，两条轨道各传三十二 MiB，结束时在途请求、字节和预留注册预算归零。",
        "action": "在已配置客户端目录执行 python3 run_demo.py read --output receipts-read-新编号。",
        "observe": "full_content_verified: True；两次 xxh3 相同；rail_bytes=[33554432,33554432]；direct=1。",
    },
    {
        "id": "performance",
        "title": "固定调度的对照，回答优化是否值得",
        "duration": 35,
        "kind": "image",
        "visual": "direct-staging-evidence.png",
        "narration": "功能演示之后，性能用独立对照说明。这是十月九日归档实验：直接暂存与相同条带计划的 packed 模式比较，每个尺寸十个独立进程块。六十四和二百五十六 MiB 的吞吐比分别是一点一零零和一点一六一，区间与损失块均保留。",
        "action": "打开作品介绍第4页。先说对照保持相同调度，再读两个主结果。",
        "observe": "明确是归档软件 RXE 实验，两个尺寸分别比较。",
    },
    {
        "id": "version",
        "title": "对象更新时，拒绝发布失效版本",
        "duration": 35,
        "kind": "image",
        "visual": "generation-witness-evidence.png",
        "narration": "版本一致性由另一组实验回答。十月十二日，二十次读取与十五次唯一内容覆盖并发执行。十次返回完整内容，十次安全拒绝。其中八次明确观察到同一次读取的 Generation 前小后大，调用方缓冲区保持原值。",
        "action": "打开作品介绍第5页。指出10、8、2的不同含义，再读一个1到2的例子。",
        "observe": "这是两次查询之间的版本切换见证，不定位精确提交时刻。",
    },
    {
        "id": "late",
        "title": "真实操作：旧库污染，修复版安全复用",
        "duration": 40,
        "kind": "comparison",
        "visual": "late-write-comparison.png",
        "narration": "接下来执行 late。旧库报错以后 QP 仍然存活，复用哨兵被晚到写入改成服务端 payload，哈希完全一致。修复版先退休 QP，探针保留原注册内存并等待服务端终态，再检查哨兵。随后同一个句柄重连，实际 PUT 和 GET 内容完整。",
        "action": "执行 python3 run_demo.py late --output receipts-late-新编号。等待约30秒；不要提前关进程或注销MR。",
        "observe": "旧库 exact_payload_corruption: True；修复版 reused_sentinel_intact: True；same_handle_reconnect...: True。",
    },
    {
        "id": "put",
        "title": "真实操作：排空旧完成事件，三轮重连恢复",
        "duration": 35,
        "kind": "terminal",
        "narration": "最后执行 put。测试观察器暂时隐藏 CQ 轮询结果，退休 QP 后读取真实完成事件。旧完成事件已在错误返回前排空。随后同一句柄、同一注册区域连续三轮 PUT 和 GET 都通过。这里验证事件顺序与完整内容，允许 RXE 复用 QP 编号。",
        "action": "执行 python3 run_demo.py put --output receipts-put-新编号。先解释受控CQ观察，再等实际完成。",
        "observe": "old_qp_real_completion...: True；same_registered_buffer_reused: True；rounds: 3；passed: True。",
    },
    {
        "id": "reproduce",
        "title": "交付入口：代码、PDF、演示脚本与完整证据",
        "duration": 30,
        "kind": "summary",
        "narration": "演示到这里，评委应看到三件事：多路径如何组成一次对象读取，直接有序暂存如何降低软件开销，以及版本变化和异步错误时如何守住边界。仓库首页提供作品介绍、演示视频、操作提词卡和完整原始收据，可以按同一输入和命令复查。",
        "action": "返回仓库首页，依次点开PDF、视频和原始证据链接。",
        "observe": "说明当前验证环境为两台KVM、双RXE；正式提交与代码推送分别记录。",
    },
]
assert sum(x["duration"] for x in segments) == 270
(ROOT / "video-scenes.json").write_text(
    json.dumps(
        {
            "duration_seconds": 270,
            "source_commit": CORE,
            "segments": segments,
            "scope": "real 2026-10-16 timed terminal capture plus mechanism images and explicitly dated archived evidence",
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
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
        "先打开PDF第3、4、5页和终端，终端字号保持可读。每条命令执行前说“我要验证什么”，结果出来后只指出2到3个关键字段；等待时解释机制。结尾回到仓库首页，展示PDF、视频、提词卡和原始收据入口。"
    ),
]
build("RailFuse-demo-guide-v2.pdf", guide, "RailFuse ContextStore - Live Demo Guide V2")
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
print("Built 9-page project PDF, 3-page guide and 270-second scene script")
