"""Turn a checked letter.draft.md into letter.md, PDF and DOCX (via LibreOffice).

Draft front matter:
  profile: ch | uk          sender profile from letter-rules.yaml (default ch)
  to: |                     recipient block, one line per line
    Hiring Team
    Example AG
  date: 15 October 2026     (default: today)
  subject: Application for Senior Software Engineer
"""
import datetime as dt
import html
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from .check import check, load_rules, report_text, strip_tags
from .jobs import job_dir, load, slugify, split_front_matter


def inline(text):
    text = html.escape(text)
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)


BULLET_RE = re.compile(r"^[-*]\s+")


def body_html(body):
    out = []
    for block in re.split(r"\n\s*\n", body.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if all(BULLET_RE.match(line) for line in lines):
            # (no backslashes inside f-strings: that needs Python 3.12, the project supports 3.10+)
            items = "".join("<li>" + inline(BULLET_RE.sub("", line)) + "</li>" for line in lines)
            out.append(f"<ul>{items}</ul>")
        else:
            out.append("<p>" + "<br>".join(inline(line) for line in lines) + "</p>")
    return "\n".join(out)


def parts(d):
    """The letter's pieces from a draft: sender name and contact lines, date, recipient lines, subject, clean body."""
    meta, body = split_front_matter((d / "letter.draft.md").read_text(encoding="utf-8"))
    rules = load_rules()
    sender = rules["senders"][meta.get("profile", "ch")]
    company = load(d).get("company", "")
    email = sender["email"].format(company=slugify(company, 30).replace("-", ""))
    return {"name": sender["name"], "contact": [*sender.get("address_lines", []), email, sender["phone"]],
            "date": str(meta.get("date") or dt.date.today().strftime("%-d %B %Y")),
            "to": str(meta.get("to", "")).strip().splitlines(), "subject": meta.get("subject") or "",
            "body": strip_tags(body), "company": company}


def page_html(p):
    contact = "<br>".join(html.escape(x) for x in p["contact"])
    to = "<br>".join(html.escape(line) for line in p["to"])
    subject = f"<p><b>{html.escape(p['subject'])}</b></p>" if p["subject"] else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<style>
 body {{ font-family: 'Liberation Serif', 'Times New Roman', serif; font-size: 11.5pt; line-height: 1.35; }}
 .sender {{ text-align: right; }} .name {{ font-size: 15pt; font-weight: bold; }}
 p {{ margin: 0 0 9pt 0; }} ul {{ margin: 0 0 9pt 0; }} li {{ margin-bottom: 4pt; }}
</style></head><body>
<p class="sender"><span class="name">{html.escape(p['name'])}</span><br>{contact}</p>
<p>{html.escape(p['date'])}</p>
<p>{to}</p>
{subject}
{body_html(p['body'])}
</body></html>"""


def soffice(p, stem, d):
    """PDF and DOCX of the letter via LibreOffice, saved in the job folder as <stem>.pdf/.docx."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"{stem}.html"
        src.write_text(page_html(p), encoding="utf-8")
        for target in ["pdf:writer_pdf_Export", "docx:MS Word 2007 XML"]:
            subprocess.run(["soffice", "--headless", "--infilter=HTML (StarWriter)", "--convert-to", target,
                            "--outdir", tmp, str(src)], check=True, capture_output=True, timeout=180)
        outputs = []
        for ext in ("pdf", "docx"):
            shutil.copy(Path(tmp) / f"{stem}.{ext}", d / f"{stem}.{ext}")
            outputs.append(d / f"{stem}.{ext}")
    return outputs


CONVERT = soffice  # the browser version swaps in its own converter (web/letterdoc.py)


def build(job, force=False):
    d = job_dir(job)
    result = check(d)
    (d / "letter.check.txt").write_text(report_text(result) + "\n")
    if result["errors"] and not force:
        return None, result

    p = parts(d)
    (d / "letter.md").write_text(p["body"], encoding="utf-8")
    stem = f"{dt.date.today().isoformat()}-{p['name'].replace(' ', '')}-CoverLetter-{slugify(p['company'], 30)}"
    return CONVERT(p, stem, d), result
