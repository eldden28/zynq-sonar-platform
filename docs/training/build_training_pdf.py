#!/usr/bin/env python3
"""Render the companion training Markdown as a paginated PDF handout.

Requires reportlab. Run with the bundled workspace Python when available.
"""
from __future__ import annotations

import html
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    PageBreak, Paragraph, Preformatted, SimpleDocTemplate,
    Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "docs/training/xilinx-ssh-hil-training-guide.md"
OUTPUT = ROOT / "output/pdf/xilinx-ssh-hil-training-guide.pdf"
FONT_ROOT = Path("/usr/share/fonts/truetype/dejavu")
for name, filename in (
    ("Guide", "DejaVuSans.ttf"),
    ("GuideBold", "DejaVuSans-Bold.ttf"),
    ("GuideMono", "DejaVuSansMono.ttf"),
):
    pdfmetrics.registerFont(TTFont(name, str(FONT_ROOT / filename)))
pdfmetrics.registerFontFamily(
    "Guide", normal="Guide", bold="GuideBold", italic="Guide",
    boldItalic="GuideBold",
)

NAVY = colors.HexColor("#15344c")
TEAL = colors.HexColor("#087e8b")
MUTED = colors.HexColor("#566674")
WIDTH = letter[0] - 92
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(
    "GuideBody", fontName="Guide", fontSize=9.1, leading=13.2,
    textColor=NAVY, spaceAfter=7,
))
styles.add(ParagraphStyle(
    "GuideTitle", parent=styles["GuideBody"], fontName="GuideBold",
    fontSize=30, leading=35, spaceBefore=28, spaceAfter=18,
))
styles.add(ParagraphStyle(
    "GuideH2", parent=styles["GuideBody"], fontName="GuideBold",
    fontSize=18, leading=23, spaceAfter=13, keepWithNext=True,
))
styles.add(ParagraphStyle(
    "GuideH3", parent=styles["GuideBody"], fontName="GuideBold",
    fontSize=10.5, leading=15, textColor=TEAL,
    spaceBefore=7, spaceAfter=6, keepWithNext=True,
))
styles.add(ParagraphStyle(
    "GuideCell", parent=styles["GuideBody"], fontSize=8,
    leading=11, spaceAfter=0, splitLongWords=True,
))
styles.add(ParagraphStyle(
    "GuideCode", fontName="GuideMono", fontSize=7.1, leading=10.4,
    leftIndent=9, rightIndent=5, borderPadding=8,
    backColor=colors.HexColor("#edf3f6"), spaceBefore=3, spaceAfter=10,
))


def inline(raw: str) -> str:
    # Protect inline code, whose paths may contain Markdown punctuation.
    pieces = re.split(r"(`[^`]+`)", raw)
    rendered = []
    for piece in pieces:
        if piece.startswith("`") and piece.endswith("`"):
            rendered.append('<font name="GuideMono" size="7.6">'
                            + html.escape(piece[1:-1]) + "</font>")
            continue
        piece = html.escape(piece)
        piece = re.sub(
            r"\[([^]]+)\]\((https?://[^)]+)\)",
            r'<link href="\2" color="#087e8b"><u>\1</u></link>', piece,
        )
        piece = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", piece)
        rendered.append(piece)
    return "".join(rendered)


def table_flow(lines: list[str]) -> Table:
    reference_table = "Source path, relative" in lines[0]
    cell_style = styles["GuideCell"]
    if reference_table:
        cell_style = ParagraphStyle(
            "ReferenceCell", parent=cell_style, fontSize=7.2, leading=9.7,
        )
    rows = []
    for line in lines:
        cells = [x.strip() for x in line.strip().strip("|").split("|")]
        if all(re.fullmatch(r":?-+:?", c) for c in cells):
            continue
        rendered = [inline(c) for c in cells]
        if reference_table:
            rendered = [c.replace('size="7.6"', 'size="6.8"') for c in rendered]
        rows.append([Paragraph(c, cell_style) for c in rendered])
    count = len(rows[0])
    if reference_table:
        widths = [WIDTH * .68, WIDTH * .32]
    elif count == 2:
        widths = [WIDTH * .49, WIDTH * .51]
    elif count == 4:
        widths = [WIDTH * .13, WIDTH * .24, WIDTH * .43, WIDTH * .20]
    else:
        widths = [WIDTH / count] * count
    table = Table(rows, colWidths=widths, hAlign="LEFT", repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9e9ed")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1),
         [colors.HexColor("#f2f6f8"), colors.white]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 7),
        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
        ("TOPPADDING", (0, 0), (-1, -1), 4 if reference_table else 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4 if reference_table else 6),
        ("LINEBELOW", (0, 0), (-1, 0), .6, TEAL),
    ]))
    return table


def decorate(canvas, doc):
    canvas.saveState()
    canvas.setFillColor(TEAL)
    canvas.rect(0, letter[1] - 9, letter[0], 9, fill=True, stroke=False)
    canvas.setFont("GuideBold", 8)
    canvas.setFillColor(MUTED)
    canvas.drawString(46, letter[1] - 32, "ZYNQ SONAR PLATFORM  /  ENGINEERING TRAINING")
    canvas.setStrokeColor(colors.HexColor("#d5e1e6"))
    canvas.line(46, 36, letter[0] - 46, 36)
    canvas.setFont("Guide", 7)
    canvas.drawString(46, 24, "Xilinx toolchain + SSH HIL  |  23 October 2026")
    canvas.drawRightString(letter[0] - 46, 24, str(doc.page))
    canvas.restoreState()


def main():
    lines = SOURCE.read_text().splitlines()
    story = []
    index = 0
    while index < len(lines):
        line = lines[index]
        if not line.strip():
            index += 1
            continue
        if line == "<!-- pagebreak -->":
            story.append(PageBreak())
            index += 1
            continue
        if line.startswith("```"):
            code = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                code.append(lines[index])
                index += 1
            # Long lines indicate a source/layout issue; never silently clip.
            for item in code:
                assert pdfmetrics.stringWidth(item, "GuideMono", 7.1) < WIDTH - 18, item
            story.append(Preformatted("\n".join(code), styles["GuideCode"]))
            index += 1
            continue
        if line.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            story.append(table_flow(table_lines))
            story.append(Spacer(1, 9))
            continue
        heading = re.match(r"^(#{1,3}) (.*)$", line)
        if heading:
            style = {1: "GuideTitle", 2: "GuideH2", 3: "GuideH3"}[len(heading[1])]
            story.append(Paragraph(inline(heading[2]), styles[style]))
            index += 1
            continue
        bullet = re.match(r"^(- |\d+\. )(.*)$", line)
        if bullet:
            marker = "-" if bullet[1] == "- " else bullet[1].strip()
            story.append(Paragraph(inline(bullet[2]), ParagraphStyle(
                "ListItem", parent=styles["GuideBody"], leftIndent=14,
                firstLineIndent=0, bulletIndent=0, spaceAfter=4,
                bulletFontName="Guide", bulletFontSize=9,
            ), bulletText=marker))
            index += 1
            continue
        paragraph = []
        while index < len(lines) and lines[index].strip():
            part = lines[index]
            if part.startswith(("#", "```", "|", "<!--")):
                break
            if paragraph and re.match(r"^(- |\d+\. )", part):
                break
            paragraph.append(part.removeprefix("> ").rstrip())
            index += 1
        story.append(Paragraph(inline(" ".join(paragraph)), styles["GuideBody"]))
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    document = SimpleDocTemplate(
        str(OUTPUT), pagesize=letter, leftMargin=46, rightMargin=46,
        topMargin=53, bottomMargin=49,
        title="Xilinx Toolchain and SSH HIL Testing",
        author="Zynq Sonar Platform / Codex",
        subject="Training guide: build integration, HIL, regression, automation",
    )
    document.build(story, onFirstPage=decorate, onLaterPages=decorate)
    print(OUTPUT)


if __name__ == "__main__":
    main()
