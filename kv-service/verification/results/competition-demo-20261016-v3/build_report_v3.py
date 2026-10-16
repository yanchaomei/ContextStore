from __future__ import annotations

# Build a detailed report with traceable sources and a linked contents page.

import argparse
import json
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    Image,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
    KeepTogether,
)
from reportlab.platypus.tableofcontents import TableOfContents


parser = argparse.ArgumentParser()
parser.add_argument("--root", type=Path, required=True)
parser.add_argument("--evidence-root", type=Path, required=True)
args = parser.parse_args()
ROOT, EVIDENCE = args.root, args.evidence_root
data = json.loads((ROOT / "report-content.json").read_text())
pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
INK = colors.HexColor("#172B43")
BLUE = colors.HexColor("#126B8A")
GRAY = colors.HexColor("#526779")
PALE = colors.HexColor("#EEF6F8")
RULE = colors.HexColor("#CADAE4")
WIDTH = A4[0] - 38 * mm


def style(name, size, leading, **kw):
    base = dict(
        fontName="STSong-Light",
        fontSize=size,
        leading=leading,
        wordWrap="CJK",
        textColor=INK,
        spaceAfter=7,
    )
    base.update(kw)
    return ParagraphStyle(name, **base)


TITLE = style("Title", 26, 36, textColor=BLUE, spaceAfter=18)
SUB = style("Subtitle", 13, 22, textColor=GRAY)
BODY = style("Body", 10.6, 17.3)
SMALL = style("Small", 8.6, 13.1, textColor=GRAY)
H1 = style("Chapter", 15.3, 22.5, textColor=BLUE, spaceBefore=14, spaceAfter=10, keepWithNext=True)
H2 = style("Minor", 11.4, 18, textColor=BLUE, spaceBefore=8, spaceAfter=6, keepWithNext=True)
CELL = style("Cell", 9, 14, spaceAfter=0)
HEAD = style("Head", 9.2, 14.2, textColor=colors.white, spaceAfter=0)
CODE = style(
    "Code",
    8.2,
    11.8,
    fontName="Courier",
    textColor=INK,
    backColor=PALE,
    borderPadding=8,
    spaceAfter=10,
)


def para(text, st=BODY):
    # Escaping preserves literal comparisons in formulas and configuration prose.
    return Paragraph(escape(str(text)).replace("\n", "<br/>"), st)


def linked(text, url, st=SMALL):
    return Paragraph(
        f'<link href="{escape(url)}" color="#126B8A">' f"<u>{escape(text)}</u></link>", st
    )


def table(headers, rows):
    cols = len(headers)
    weights = [max(9, min(30, max(len(str(r[i])) for r in [headers] + rows))) for i in range(cols)]
    widths = [WIDTH * w / sum(weights) for w in weights]
    values = [
        [para(c, HEAD if i == 0 else CELL) for c in row] for i, row in enumerate([headers] + rows)
    ]
    t = Table(values, colWidths=widths, repeatRows=1, hAlign="LEFT")
    t.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), BLUE),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 7),
                ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                ("TOPPADDING", (0, 0), (-1, -1), 7),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("GRID", (0, 0), (-1, -1), 0.3, RULE),
            ]
        )
    )
    return t


def decor(c, doc):
    c.saveState()
    w, h = A4
    c.setStrokeColor(RULE)
    c.line(19 * mm, h - 20.5 * mm, w - 19 * mm, h - 20.5 * mm)
    c.setFont("STSong-Light", 8)
    c.setFillColor(GRAY)
    c.drawString(
        19 * mm, h - 16.5 * mm, "RailFuse ContextStore  |  详细技术说明  |  2026-10-16 / V3"
    )
    c.drawString(19 * mm, 14 * mm, "源码 / 方法 / 原始收据 / 适用边界")
    c.drawRightString(w - 19 * mm, 14 * mm, str(doc.page))
    c.restoreState()


class Report(BaseDocTemplate):
    def __init__(self, path):
        super().__init__(
            path,
            pagesize=A4,
            leftMargin=19 * mm,
            rightMargin=19 * mm,
            topMargin=27 * mm,
            bottomMargin=23 * mm,
            title=data["metadata"]["title"],
            author="RailFuse ContextStore",
        )
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="body",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(PageTemplate(id="report", frames=frame, onPage=decor))

    def afterFlowable(self, flowable):
        if isinstance(flowable, Paragraph) and hasattr(flowable, "chapter_key"):
            text = flowable.getPlainText()
            key = flowable.chapter_key
            self.canv.bookmarkPage(key)
            self.canv.addOutlineEntry(text, key, 0)
            self.notify("TOCEntry", (0, text, self.page, key))


def heading(text, key):
    p = para(text, H1)
    p.chapter_key = key
    return p


story = [
    Spacer(1, 12),
    para("RailFuse ContextStore", SUB),
    para("面向版本化 KV Cache 对象的\n故障原子多轨读取引擎", TITLE),
    para(data["metadata"]["subtitle"], SUB),
    Spacer(1, 12),
    para("以更少的内存搬运，交付可验证的完整对象", H1),
    para(
        "一次对象读取需要同时回答：怎样分配路径，何时发布结果，何时回收内存。"
        "作品沿用 DaoCloud ContextStore 的服务、协议与条带布局，扩展客户端读取契约，"
        "并通过真实 Verbs/RXE 的失败反例验证异步错误后的缓冲区复用与恢复。"
    ),
    Spacer(1, 8),
    table(
        ["问题", "机制", "可复核证据"],
        [
            [
                "安全暂存有额外组装开销",
                "有条件直接写入私有有序暂存；保留最终发布复制",
                "同调度 10 个配对进程块：64 / 256 MiB 分别 1.100× / 1.161×",
            ],
            [
                "读取中发生对象覆盖",
                "传输后复查完整对象身份与 Placement",
                "20 次读中 8 次明确观察到 Generation 增长并拒绝发布",
            ],
            [
                "错误返回后仍有 DMA / 旧完成",
                "先退休 QP、排空 CQ，再复用与重连",
                "旧库精确污染负对照；修复版同 MR 哨兵、GET / PUT 恢复",
            ],
        ],
    ),
    Spacer(1, 12),
    para("阅读方式", H2),
    para(
        "第 1-3 章说明项目与边界，第 4-13 章解释实现与取舍，"
        "第 14-19 章报告分层证据，第 20 章给出部署与下一步验证。"
        "附录提供配置、公共接口和可复查命令，末尾列出 32 个源码及证据入口。"
    ),
    para(
        "环境边界：当前实测包含真实软件 RoCE / RXE；没有物理双 HCA 聚合或端到端推理增益的测量。"
        "历史实验按原始日期分别报告。本次 V3 只更新材料和配音，未修改核心运行时。",
        SMALL,
    ),
    para("核心安全提交：" + data["metadata"]["core_safety_commit"], SMALL),
    linked("代码与最新材料入口", data["metadata"]["repo_url"]),
    PageBreak(),
]

story += [
    para("目录", TITLE),
    para("页码与书签可点击跳转；章节连续排版，图、表和代码紧随相应说明。", SMALL),
]
toc = TableOfContents()
toc.levelStyles = [
    style("TOC", 9.5, 14, spaceBefore=0, spaceAfter=0, leftIndent=0, firstLineIndent=0)
]
story += [toc, PageBreak()]

for section in data["sections"] + data["appendices"]:
    ident = section["id"]
    title = section["title"] if ident.isalpha() else ident + "  " + section["title"]
    story.append(heading(title, "section-" + ident))
    figures = []
    for fig in section.get("figures", []):
        path = EVIDENCE / "assets" / fig["file"]
        with PILImage.open(path) as im:
            iw, ih = im.size
        width = min(WIDTH, 420 * iw / ih)
        figures.append(
            KeepTogether(
                [
                    Spacer(1, 7),
                    Image(str(path), width=width, height=width * ih / iw),
                    para(fig["caption"], SMALL),
                ]
            )
        )
    for index, text in enumerate(section.get("paragraphs", [])):
        story.append(para(text))
        if index == 1:
            story.extend(figures)
            figures = []
    story.extend(figures)
    for item in section.get("tables", []):
        story += [para(item["title"], H2), table(item["headers"], item["rows"]), Spacer(1, 7)]
    for block in section.get("code_blocks", []):
        code = block["code"]
        if max(map(len, code.splitlines()), default=0) > 92:
            code = code.replace(
                "bash kv-service/benchmarks/run_rxe_five_arm_20261009.sh   ",
                "bash kv-service/benchmarks/run_rxe_five_arm_20261009.sh \\\n+  ",
            )
        lines = code.splitlines()
        story.append(
            KeepTogether(
                [
                    para(block["caption"] + " / " + block["language"], H2),
                    Preformatted("\n".join(lines[:3]), CODE),
                ]
            )
        )
        if len(lines) > 3:
            story.append(Preformatted("\n".join(lines[3:]), CODE))
    refs = "  ".join(
        f'<link href="#source-{r}" color="#126B8A">[{r}]</link>'
        for r in section.get("evidence_refs", [])
    )
    story += [Paragraph("源码与证据：" + refs, SMALL), Spacer(1, 6)]

story += [
    heading("源码与证据索引", "source-index"),
    para(
        "源码及实验输入链接固定到 V3 制作前的 2705d2e 提交，便于评委核对当前叙述。"
        "目录链接指向相同提交的树；最新 PDF、视频入口由仓库首页维护。",
        SMALL,
    ),
]
SOURCEHEAD = style(
    "SourceHead", 10.2, 15, textColor=BLUE, spaceBefore=3, spaceAfter=2, keepWithNext=True
)
SOURCEPATH = style("SourcePath", 8.2, 12, textColor=GRAY, spaceAfter=5)
for source in data["sources"]:
    story.append(
        Paragraph(
            f'<a name="source-{source["id"]}"/>' + escape(source["id"] + "  " + source["purpose"]),
            SOURCEHEAD,
        )
    )
    url = source["url"].replace(
        "/research/direct-ordered-staging/", "/" + data["metadata"]["source_head"] + "/"
    )
    if source["path"].endswith("/"):
        url = url.replace("/blob/", "/tree/")
    story.append(linked(source["path"], url, SOURCEPATH))

Report(str(ROOT / "RailFuse-project-introduction-v3.pdf")).multiBuild(story)
print(ROOT / "RailFuse-project-introduction-v3.pdf")
