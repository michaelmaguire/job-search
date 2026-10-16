"""Letter PDF and DOCX without LibreOffice, for the browser version (render.CONVERT is swapped for write()).

Same content and layout as render.page_html - sender block on the right, date, recipient, bold subject,
paragraphs and bullet lists with **bold** - drawn with fpdf2 (Times, A4) and written as a minimal Word
document. The result looks close to, not identical with, the LibreOffice output of the local app.
"""
import io
import re
import unicodedata
import zipfile
from xml.sax.saxutils import escape

from jobsearch.render import BULLET_RE

SIZE, NAME_SIZE, LEADING, GAP = 11.5, 15, 1.35, 9  # points, as in render.page_html
MARGIN, EDGE = 56.7, 28.35  # left 2 cm, other sides 1 cm - as LibreOffice lays out the local app's letter


def blocks(body):
    """[('p', [line, ...]) | ('ul', [item, ...])] - the paragraph structure render.body_html uses."""
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if lines and all(BULLET_RE.match(line) for line in lines):
            out.append(("ul", [BULLET_RE.sub("", line) for line in lines]))
        elif lines:
            out.append(("p", lines))
    return out


def runs(text):
    """'a **b** c' -> [('a ', False), ('b', True), (' c', False)]."""
    return [(part, i % 2 == 1) for i, part in enumerate(re.split(r"\*\*(.+?)\*\*", text)) if part]


def cp1252(text):
    """The PDF core fonts cover Windows-1252: keep accents, quotes and dashes; reduce anything else."""
    out = []
    for ch in text:
        try:
            ch.encode("cp1252")
            out.append(ch)
        except UnicodeEncodeError:
            base = unicodedata.normalize("NFKD", ch).encode("cp1252", "ignore").decode("cp1252")
            out.append(base or "?")
    return "".join(out)


def pdf_bytes(p):
    from fpdf import FPDF

    pdf = FPDF(unit="pt", format="A4")
    pdf.core_fonts_encoding = "cp1252"
    pdf.set_margins(MARGIN, EDGE, EDGE)
    pdf.set_auto_page_break(True, EDGE)
    pdf.add_page()
    line = SIZE * LEADING

    def gap():
        pdf.ln(GAP)

    pdf.set_font("Times", "B", NAME_SIZE)
    pdf.cell(0, NAME_SIZE * LEADING, cp1252(p["name"]), align="R", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Times", "", SIZE)
    for c in p["contact"]:
        pdf.cell(0, line, cp1252(c), align="R", new_x="LMARGIN", new_y="NEXT")
    gap()

    def write(text, bold_all=False):
        for part, bold in runs(text):
            pdf.set_font("Times", "B" if bold or bold_all else "", SIZE)
            pdf.write(line, cp1252(part))
        pdf.set_font("Times", "", SIZE)

    def paragraph(lines, bold_all=False):
        for text in lines:
            write(text, bold_all)
            pdf.ln(line)
        gap()

    paragraph([p["date"]])
    if p["to"]:
        paragraph(p["to"])
    if p["subject"]:
        paragraph([p["subject"]], bold_all=True)
    for kind, lines in blocks(p["body"]):
        if kind == "p":
            paragraph(lines)
            continue
        for item in lines:
            pdf.set_x(MARGIN + 12)
            pdf.write(line, "\u2022")  # in Windows-1252, so the core font has it
            pdf.set_left_margin(MARGIN + 24)
            pdf.set_x(MARGIN + 24)
            write(item)
            pdf.ln(line + 4)
            pdf.set_left_margin(MARGIN)
        gap()
    return bytes(pdf.output())


# ---------------------------------------------------------------- DOCX

def _run(text, bold=False, size=SIZE):
    props = ("<w:b/>" if bold else "") + f'<w:sz w:val="{round(size * 2)}"/>'
    return f'<w:r><w:rPr>{props}</w:rPr><w:t xml:space="preserve">{escape(text)}</w:t></w:r>'


def _para(content, right=False, indent=False, after=GAP):
    props = f'<w:spacing w:after="{round(after * 20)}" w:line="{round(SIZE * LEADING * 20)}" w:lineRule="exact"/>'
    if indent:
        props += '<w:ind w:left="480" w:hanging="240"/>'
    if right:
        props += '<w:jc w:val="right"/>'
    return f"<w:p><w:pPr>{props}</w:pPr>{content}</w:p>"


BR = "<w:r><w:br/></w:r>"


def _lines(lines, bold_all=False):
    return BR.join("".join(_run(t, bold or bold_all) for t, bold in runs(text)) for text in lines)


def docx_bytes(p):
    body = [_para(_run(p["name"], True, NAME_SIZE) + BR + BR.join(_run(c) for c in p["contact"]), right=True),
            _para(_run(p["date"]))]
    if p["to"]:
        body.append(_para(_lines(p["to"])))
    if p["subject"]:
        body.append(_para(_lines([p["subject"]], bold_all=True)))
    for kind, lines in blocks(p["body"]):
        if kind == "p":
            body.append(_para(_lines(lines)))
        else:
            body += [_para(_run("•\t") + _lines([item]), indent=True, after=4 if i < len(lines) - 1 else GAP)
                     for i, item in enumerate(lines)]
    w = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {w}><w:body>{"".join(body)}'
                '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="567" w:right="567" w:bottom="567" w:left="1134" w:header="0" w:footer="0" w:gutter="0"/>'
                "</w:sectPr></w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {w}><w:docDefaults><w:rPrDefault><w:rPr>'
              '<w:rFonts w:ascii="Times New Roman" w:hAnsi="Times New Roman" w:cs="Times New Roman"/>'
              f'<w:sz w:val="{round(SIZE * 2)}"/><w:lang w:val="en-GB"/></w:rPr></w:rPrDefault></w:docDefaults></w:styles>')
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    files = {
        "[Content_Types].xml": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
        "</Types>",
        "_rels/.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/officeDocument" Target="word/document.xml"/></Relationships>',
        "word/_rels/document.xml.rels": '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        f'<Relationship Id="rId1" Type="{rel}/styles" Target="styles.xml"/></Relationships>',
        "word/document.xml": document,
        "word/styles.xml": styles,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in files.items():
            z.writestr(name, text)
    return buf.getvalue()


def write(p, stem, d):
    """render.CONVERT for the browser: <stem>.pdf and <stem>.docx in the job folder."""
    outputs = [d / f"{stem}.pdf", d / f"{stem}.docx"]
    outputs[0].write_bytes(pdf_bytes(p))
    outputs[1].write_bytes(docx_bytes(p))
    return outputs
