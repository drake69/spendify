"""
Genera brand_analysis_v1.pdf da brand_analysis_v1.md
"""
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_LEFT, TA_CENTER, TA_RIGHT
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle,
    HRFlowable, PageBreak, KeepTogether
)
from reportlab.platypus.tableofcontents import TableOfContents
from reportlab.lib.colors import HexColor
import re, os

# ── Colours ───────────────────────────────────────────────────────────────────
PRIMARY   = HexColor("#1A3A5C")
ACCENT    = HexColor("#2563A8")
LIGHTBLUE = HexColor("#D6E4F0")
ROWALT    = HexColor("#EEF4FA")
DARKGRAY  = HexColor("#1A1A1A")
RRED      = HexColor("#C00000")
GGREEN    = HexColor("#1E6B3C")
AMBER     = HexColor("#BF8F00")
WHITE     = colors.white
LIGHTGRAY = HexColor("#F5F5F5")
MIDGRAY   = HexColor("#888888")

PAGE_W, PAGE_H = A4
MARGIN = 2.5 * cm

# ── Styles ────────────────────────────────────────────────────────────────────
styles = getSampleStyleSheet()

def make_style(name, parent="Normal", **kw):
    s = ParagraphStyle(name, parent=styles[parent], **kw)
    return s

sTitle = make_style("sTitle", fontSize=28, textColor=WHITE,
                    spaceAfter=6, leading=34, alignment=TA_LEFT)
sSubtitle = make_style("sSubtitle", fontSize=13, textColor=HexColor("#CCE0F5"),
                       spaceAfter=4, leading=18, alignment=TA_LEFT)
sMeta = make_style("sMeta", fontSize=9, textColor=HexColor("#AAC4E0"),
                   spaceAfter=2, leading=12, alignment=TA_LEFT)

sH1 = make_style("sH1", fontSize=16, textColor=PRIMARY, fontName="Helvetica-Bold",
                 spaceBefore=18, spaceAfter=4, leading=20)
sH2 = make_style("sH2", fontSize=13, textColor=ACCENT, fontName="Helvetica-Bold",
                 spaceBefore=14, spaceAfter=3, leading=17)
sH3 = make_style("sH3", fontSize=11, textColor=DARKGRAY, fontName="Helvetica-Bold",
                 spaceBefore=10, spaceAfter=2, leading=15)
sBody = make_style("sBody", fontSize=9.5, textColor=DARKGRAY,
                   spaceAfter=4, leading=14)
sBullet = make_style("sBullet", fontSize=9.5, textColor=DARKGRAY,
                     leftIndent=14, spaceAfter=2, leading=13,
                     bulletIndent=4)
sCode = make_style("sCode", fontSize=8.5, textColor=DARKGRAY,
                   fontName="Courier", leftIndent=10,
                   spaceAfter=3, leading=12,
                   backColor=LIGHTGRAY, borderPadding=4)
sNote = make_style("sNote", fontSize=8.5, textColor=MIDGRAY,
                   spaceAfter=3, leading=12, leftIndent=10)
sTH = make_style("sTH", fontSize=8.5, textColor=WHITE, fontName="Helvetica-Bold",
                 alignment=TA_CENTER, leading=11)
sTD = make_style("sTD", fontSize=8.5, textColor=DARKGRAY, leading=11, spaceAfter=0)
sTDA = make_style("sTDA", fontSize=8.5, textColor=DARKGRAY, leading=11, backColor=ROWALT)
sCaption = make_style("sCaption", fontSize=8, textColor=MIDGRAY,
                      fontName="Helvetica-Oblique", spaceAfter=6, leading=11, alignment=TA_CENTER)
sPageNum = make_style("sPageNum", fontSize=8, textColor=MIDGRAY, alignment=TA_RIGHT)
sTocH1 = make_style("sTocH1", fontSize=10, textColor=PRIMARY, fontName="Helvetica-Bold",
                    leading=14, spaceAfter=2)
sTocH2 = make_style("sTocH2", fontSize=9, textColor=ACCENT, leftIndent=12,
                    leading=13, spaceAfter=1)

# ── Header/Footer ─────────────────────────────────────────────────────────────
def on_page(canvas, doc):
    canvas.saveState()
    w, h = A4
    # Header line
    canvas.setStrokeColor(ACCENT)
    canvas.setLineWidth(0.6)
    canvas.line(MARGIN, h - 1.5*cm, w - MARGIN, h - 1.5*cm)
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(MIDGRAY)
    canvas.drawString(MARGIN, h - 1.25*cm, "Brand Analysis v1  ·  Naming & Identity")
    canvas.drawRightString(w - MARGIN, h - 1.25*cm, "Uso Interno – Riservato")
    # Footer
    canvas.line(MARGIN, 1.4*cm, w - MARGIN, 1.4*cm)
    canvas.drawRightString(w - MARGIN, 0.9*cm, f"Pag. {doc.page}")
    canvas.restoreState()

def on_first_page(canvas, doc):
    canvas.saveState()
    w, h = A4
    # Blue header bar
    canvas.setFillColor(PRIMARY)
    canvas.rect(0, h - 5.2*cm, w, 5.2*cm, fill=1, stroke=0)
    canvas.setFillColor(ACCENT)
    canvas.rect(0, h - 5.8*cm, w, 0.6*cm, fill=1, stroke=0)
    canvas.restoreState()

# ── Markdown parser (minimal) ──────────────────────────────────────────────────
def parse_md_to_flowables(md_text):
    lines = md_text.split("\n")
    flowables = []
    i = 0
    in_table = False
    table_rows = []
    in_note = False

    def flush_table():
        nonlocal table_rows, in_table
        if not table_rows:
            return
        # Remove separator rows (---|---|---)
        data_rows = [r for r in table_rows if not all(
            re.match(r"^[-:]+$", c.strip()) for c in r if c.strip()
        )]
        if not data_rows:
            table_rows = []
            in_table = False
            return

        col_n = max(len(r) for r in data_rows)
        # Pad rows
        data_rows = [r + [""] * (col_n - len(r)) for r in data_rows]

        tdata = []
        for ri, row in enumerate(data_rows):
            if ri == 0:
                tdata.append([Paragraph(c.strip(), sTH) for c in row])
            elif ri % 2 == 0:
                tdata.append([Paragraph(c.strip(), sTDA) for c in row])
            else:
                tdata.append([Paragraph(c.strip(), sTD) for c in row])

        avail = PAGE_W - 2 * MARGIN
        col_w = avail / col_n

        ts = TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PRIMARY),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [WHITE, ROWALT]),
            ("GRID", (0, 0), (-1, -1), 0.4, HexColor("#C0CCE0")),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ])
        t = Table(tdata, colWidths=[col_w] * col_n)
        t.setStyle(ts)
        flowables.append(Spacer(1, 6))
        flowables.append(t)
        flowables.append(Spacer(1, 6))
        table_rows = []
        in_table = False

    def text_format(s):
        """Convert basic markdown inline to reportlab markup."""
        # Bold
        s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
        # Italic
        s = re.sub(r"\*(.+?)\*", r"<i>\1</i>", s)
        # Code
        s = re.sub(r"`(.+?)`", r'<font name="Courier">\1</font>', s)
        # Escape < > except tags we just added
        # Already done via reportlab paragraph handling
        return s

    first_page_done = False
    section_counter = [0]

    while i < len(lines):
        line = lines[i]

        # Skip front matter / hr
        if line.strip() == "---":
            if i > 5:  # not front matter
                flowables.append(HRFlowable(width="100%", thickness=0.5,
                                            color=ACCENT, spaceAfter=6, spaceBefore=6))
            i += 1
            continue

        # Table row
        if line.strip().startswith("|"):
            if in_table is False:
                flush_table()
            in_table = True
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            table_rows.append(cells)
            i += 1
            continue
        else:
            if in_table:
                flush_table()

        # Blank line
        if line.strip() == "":
            flowables.append(Spacer(1, 4))
            i += 1
            continue

        # H1
        if line.startswith("# ") and not line.startswith("## "):
            flush_table()
            txt = line[2:].strip()
            if txt.startswith("Brand Analysis"):
                # Cover page title — handled by canvas; skip text flowable
                # but add spacer for cover
                flowables.append(Spacer(1, 4.5*cm))
                flowables.append(Paragraph(txt, sTitle))
                flowables.append(Paragraph("Naming & Identity · Draft per validazione", sSubtitle))
                flowables.append(Paragraph("Versione 1.0  ·  Marzo 2026  ·  Uso Interno", sMeta))
                flowables.append(Spacer(1, 1*cm))
            else:
                section_counter[0] += 1
                flowables.append(PageBreak())
                flowables.append(Paragraph(f"{section_counter[0]}. {txt}", sH1))
                flowables.append(HRFlowable(width="100%", thickness=1.2,
                                            color=ACCENT, spaceAfter=6))
            i += 1
            continue

        # H2
        if line.startswith("## "):
            flush_table()
            txt = line[3:].strip()
            flowables.append(Paragraph(text_format(txt), sH2))
            i += 1
            continue

        # H3
        if line.startswith("### "):
            flush_table()
            txt = line[4:].strip()
            flowables.append(Paragraph(text_format(txt), sH3))
            i += 1
            continue

        # H4
        if line.startswith("#### "):
            flush_table()
            txt = line[5:].strip()
            flowables.append(Paragraph(f"<b>{text_format(txt)}</b>", sBody))
            i += 1
            continue

        # Blockquote / note
        if line.startswith("> "):
            flush_table()
            txt = line[2:].strip()
            flowables.append(Paragraph(f"<i>{text_format(txt)}</i>", sNote))
            i += 1
            continue

        # Ordered list
        m = re.match(r"^(\d+)\.\s+(.*)", line)
        if m:
            flush_table()
            num = m.group(1)
            txt = m.group(2)
            flowables.append(Paragraph(f"{num}. {text_format(txt)}", sBullet))
            i += 1
            continue

        # Unordered list
        if re.match(r"^[-*]\s", line):
            flush_table()
            txt = line[2:].strip()
            # checkbox
            txt = txt.replace("[ ]", "☐").replace("[x]", "☑").replace("[X]", "☑")
            flowables.append(Paragraph(f"• {text_format(txt)}", sBullet))
            i += 1
            continue

        # Sub-list (4 spaces)
        if line.startswith("   -") or line.startswith("    -"):
            flush_table()
            txt = re.sub(r"^\s+[-*]\s*", "", line).strip()
            txt = txt.replace("[ ]", "☐").replace("[x]", "☑")
            sub = make_style(f"sub{i}", parent="sBullet", leftIndent=28)
            flowables.append(Paragraph(f"◦ {text_format(txt)}", sBullet))
            i += 1
            continue

        # Code block
        if line.startswith("```"):
            flush_table()
            i += 1
            code_lines = []
            while i < len(lines) and not lines[i].startswith("```"):
                code_lines.append(lines[i])
                i += 1
            i += 1
            code_text = "\n".join(code_lines)
            flowables.append(Paragraph(code_text.replace("\n", "<br/>"), sCode))
            continue

        # Normal paragraph
        flush_table()
        txt = line.strip()
        if txt:
            flowables.append(Paragraph(text_format(txt), sBody))
        i += 1

    flush_table()
    return flowables

# ── Build PDF ─────────────────────────────────────────────────────────────────
def build_pdf(md_path, pdf_path):
    with open(md_path, encoding="utf-8") as f:
        md_text = f.read()

    doc = SimpleDocTemplate(
        pdf_path,
        pagesize=A4,
        leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=2.2*cm, bottomMargin=2*cm,
        title="Brand Analysis v1 — Naming & Identity",
        author="Spendif.ai",
        subject="Brand Analysis",
    )

    story = parse_md_to_flowables(md_text)

    doc.build(story,
              onFirstPage=on_first_page,
              onLaterPages=on_page)
    print(f"✅  PDF creato: {pdf_path}")

if __name__ == "__main__":
    base = "/Users/lcorsaro/Documents/Progetti/PERSONALE/Spendif.ai/documents"
    build_pdf(f"{base}/brand_analysis_v1.md", f"{base}/brand_analysis_v1.pdf")
