#!/usr/bin/env python3
"""Render the Cora OFDM Markdown test report as a publication-ready PDF."""

from __future__ import annotations

import argparse
from html import escape
from pathlib import Path
import re

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    ListFlowable,
    ListItem,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)


ACCENT = colors.HexColor("#1A5276")
ACCENT_DARK = colors.HexColor("#123B55")
ACCENT_LIGHT = colors.HexColor("#DCEBF3")
INK = colors.HexColor("#17202A")
MUTED = colors.HexColor("#52616B")
GRID = colors.HexColor("#AAB7B8")
PASS = colors.HexColor("#E8F5E9")
FAIL = colors.HexColor("#FDEDEC")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("docs/cora-ofdm-modulation-test-report.md"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("docs/cora-ofdm-modulation-test-report.pdf"),
    )
    return parser.parse_args()


def register_fonts() -> tuple[str, str, str]:
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    mono = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")
    if regular.exists() and bold.exists() and mono.exists():
        pdfmetrics.registerFont(TTFont("ReportSans", regular))
        pdfmetrics.registerFont(TTFont("ReportSans-Bold", bold))
        pdfmetrics.registerFont(TTFont("ReportMono", mono))
        return "ReportSans", "ReportSans-Bold", "ReportMono"
    return "Helvetica", "Helvetica-Bold", "Courier"


BODY_FONT, BOLD_FONT, MONO_FONT = register_fonts()


def inline_markup(text: str) -> str:
    """Escape text and retain the small inline Markdown subset in the report."""
    placeholders: list[str] = []

    def save_code(match: re.Match[str]) -> str:
        index = len(placeholders)
        placeholders.append(
            f'<font name="{MONO_FONT}" size="7.2">'
            f"{escape(match.group(1))}</font>"
        )
        return f"@@CODE{index}@@"

    text = re.sub(r"`([^`]+)`", save_code, text)
    text = escape(text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    for index, value in enumerate(placeholders):
        text = text.replace(f"@@CODE{index}@@", value)
    return text


def report_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=BOLD_FONT,
            fontSize=22,
            leading=27,
            textColor=ACCENT_DARK,
            alignment=TA_LEFT,
            spaceAfter=12,
        ),
        "h2": ParagraphStyle(
            "ReportH2",
            parent=base["Heading2"],
            fontName=BOLD_FONT,
            fontSize=13,
            leading=16,
            textColor=ACCENT,
            spaceBefore=13,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "ReportH3",
            parent=base["Heading3"],
            fontName=BOLD_FONT,
            fontSize=10.5,
            leading=13,
            textColor=ACCENT_DARK,
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "body": ParagraphStyle(
            "ReportBody",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=8.6,
            leading=11.4,
            textColor=INK,
            spaceAfter=6,
        ),
        "date": ParagraphStyle(
            "ReportDate",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=9,
            leading=12,
            textColor=MUTED,
            spaceAfter=16,
        ),
        "bullet": ParagraphStyle(
            "ReportBullet",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=8.4,
            leading=11,
            textColor=INK,
            leftIndent=4,
        ),
        "table": ParagraphStyle(
            "ReportTable",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=6.7,
            leading=8.2,
            textColor=INK,
            alignment=TA_LEFT,
        ),
        "table_header": ParagraphStyle(
            "ReportTableHeader",
            parent=base["BodyText"],
            fontName=BOLD_FONT,
            fontSize=6.8,
            leading=8.4,
            textColor=colors.white,
            alignment=TA_LEFT,
        ),
        "footer": ParagraphStyle(
            "ReportFooter",
            parent=base["BodyText"],
            fontName=BODY_FONT,
            fontSize=7,
            leading=8,
            textColor=MUTED,
            alignment=TA_CENTER,
        ),
    }


STYLES = report_styles()


def column_widths(column_count: int, available: float) -> list[float]:
    preferred = {
        2: (0.32, 0.68),
        4: (0.17, 0.36, 0.22, 0.25),
        5: (0.18, 0.17, 0.22, 0.20, 0.23),
        6: (0.35, 0.13, 0.12, 0.14, 0.13, 0.13),
    }
    ratios = preferred.get(
        column_count,
        tuple(1.0 / column_count for _ in range(column_count)),
    )
    return [available * ratio for ratio in ratios]


def make_table(rows: list[list[str]], available: float) -> Table:
    column_count = max(len(row) for row in rows)
    normalized = [
        row + [""] * (column_count - len(row))
        for row in rows
    ]
    cells = []
    for row_index, row in enumerate(normalized):
        style = STYLES["table_header"] if row_index == 0 else STYLES["table"]
        cells.append([Paragraph(inline_markup(cell), style) for cell in row])

    table = Table(
        cells,
        colWidths=column_widths(column_count, available),
        repeatRows=1,
        hAlign="LEFT",
        splitByRow=1,
    )
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), ACCENT),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.35, GRID),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 3.2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3.2),
        ("TOPPADDING", (0, 0), (-1, -1), 3.0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.0),
    ]
    for row_index, row in enumerate(normalized[1:], start=1):
        background = colors.white if row_index % 2 else colors.HexColor("#F5F8FA")
        joined = " ".join(row).upper()
        if "FAIL" in joined:
            background = FAIL
        elif "PASS" in joined:
            background = PASS
        commands.append(("BACKGROUND", (0, row_index), (-1, row_index), background))
    table.setStyle(TableStyle(commands))
    return table


def consume_list(
    lines: list[str],
    start: int,
    ordered: bool,
) -> tuple[ListFlowable, int]:
    items: list[ListItem] = []
    index = start
    pattern = r"^\d+\.\s+" if ordered else r"^-\s+"
    while index < len(lines) and re.match(pattern, lines[index]):
        text = re.sub(pattern, "", lines[index]).strip()
        index += 1
        continuation = []
        while (
            index < len(lines)
            and lines[index].strip()
            and not re.match(r"^(#|\||-\s+|\d+\.\s+)", lines[index])
        ):
            continuation.append(lines[index].strip())
            index += 1
        if continuation:
            text += " " + " ".join(continuation)
        items.append(
            ListItem(
                Paragraph(inline_markup(text), STYLES["bullet"]),
                leftIndent=14,
            )
        )
    list_options = {
        "bulletType": "1" if ordered else "bullet",
        "leftIndent": 18,
        "bulletFontName": BODY_FONT,
        "bulletFontSize": 7,
        "spaceAfter": 7,
    }
    if ordered:
        list_options["start"] = "1"
    return ListFlowable(items, **list_options), index


def markdown_story(markdown: str, available: float) -> list:
    lines = markdown.splitlines()
    story: list = []
    index = 0
    first_paragraph = True
    while index < len(lines):
        stripped = lines[index].strip()
        if not stripped:
            index += 1
            continue
        if stripped.startswith("# "):
            story.append(Paragraph(inline_markup(stripped[2:]), STYLES["title"]))
            story.append(Table([[""]], colWidths=[available], rowHeights=[3],
                               style=[("BACKGROUND", (0, 0), (-1, -1), ACCENT)]))
            story.append(Spacer(1, 8))
            index += 1
            continue
        if stripped.startswith("## "):
            story.append(Paragraph(inline_markup(stripped[3:]), STYLES["h2"]))
            index += 1
            continue
        if stripped.startswith("### "):
            story.append(Paragraph(inline_markup(stripped[4:]), STYLES["h3"]))
            index += 1
            continue
        if stripped.startswith("|") and index + 1 < len(lines):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            rows = [
                [cell.strip() for cell in line.strip("|").split("|")]
                for line in table_lines
            ]
            if len(rows) >= 2 and all(
                re.fullmatch(r":?-{3,}:?", cell.strip())
                for cell in rows[1]
            ):
                rows.pop(1)
            story.append(make_table(rows, available))
            story.append(Spacer(1, 7))
            continue
        if re.match(r"^-\s+", stripped):
            flowable, index = consume_list(lines, index, ordered=False)
            story.append(flowable)
            continue
        if re.match(r"^\d+\.\s+", stripped):
            flowable, index = consume_list(lines, index, ordered=True)
            story.append(flowable)
            continue

        paragraph = [stripped]
        index += 1
        while index < len(lines):
            next_line = lines[index].strip()
            if (
                not next_line
                or re.match(r"^(#|\||-\s+|\d+\.\s+)", next_line)
            ):
                break
            paragraph.append(next_line)
            index += 1
        style = STYLES["date"] if first_paragraph else STYLES["body"]
        story.append(Paragraph(inline_markup(" ".join(paragraph)), style))
        first_paragraph = False
    return story


def page_decor(canvas, doc) -> None:
    canvas.saveState()
    width, height = letter
    if doc.page > 1:
        canvas.setStrokeColor(ACCENT_LIGHT)
        canvas.setLineWidth(0.6)
        canvas.line(doc.leftMargin, height - 29, width - doc.rightMargin, height - 29)
        canvas.setFont(BODY_FONT, 7)
        canvas.setFillColor(MUTED)
        canvas.drawString(
            doc.leftMargin,
            height - 23,
            "Cora Z7-10 acoustic OFDM modulation test report",
        )
    canvas.setStrokeColor(ACCENT_LIGHT)
    canvas.line(doc.leftMargin, 27, width - doc.rightMargin, 27)
    canvas.setFont(BODY_FONT, 7)
    canvas.setFillColor(MUTED)
    canvas.drawCentredString(width / 2, 17, f"Page {doc.page}")
    canvas.restoreState()


def render(input_path: Path, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    document = BaseDocTemplate(
        str(output_path),
        pagesize=letter,
        leftMargin=0.48 * inch,
        rightMargin=0.48 * inch,
        topMargin=0.48 * inch,
        bottomMargin=0.45 * inch,
        title="Cora acoustic OFDM modulation test report",
        author="Cora Z7-10 GNU Radio/PetaLinux project",
        subject="Physical acoustic OFDM modulation, coding, and framing results",
        creator="scripts/render-modulation-report.py",
    )
    frame = Frame(
        document.leftMargin,
        document.bottomMargin,
        document.width,
        document.height,
        id="report",
    )
    document.addPageTemplates(
        [PageTemplate(id="report", frames=[frame], onPage=page_decor)]
    )
    story = markdown_story(
        input_path.read_text(encoding="utf-8"),
        document.width,
    )
    document.build(story)


def main() -> None:
    args = parse_args()
    render(args.input, args.output)
    print(args.output)


if __name__ == "__main__":
    main()
