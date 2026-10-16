"""Monthly summary of applications in the layout of RAV form 716.007, in German
("Nachweis der persönlichen Arbeitsbemühungen") or Italian ("Prova degli sforzi
personali intrapresi per trovare lavoro") - the RAV/URC accepts either.

`official_pdf` fills the real fillable form 716.007 i (jobsearch/forms/716.007-i.pdf), with
your name and AHV number from ~/.config/jobsearch/insured.yaml (kept out of git).
"""
import csv
import datetime as dt
import html
import io
import os
import subprocess
from pathlib import Path

import yaml

from . import efforts
from .paths import REPORTS

FORM_IT = Path(__file__).parent / "forms" / "716.007-i.pdf"
INSURED = Path(os.environ.get("JOBSEARCH_CONFIG", Path.home() / ".config" / "jobsearch")) / "insured.yaml"
ROWS_PER_FORM = 14
HEADER_FIELDS = {"Cognome e nome", "No AVS", "Mese e anno", "Data", "Allegati"}

LANGS = {
    "de": {
        "title": "Nachweis der persönlichen Arbeitsbemühungen",
        "name": "Name und Vorname", "month": "Monat und Jahr", "count": "Anzahl Bewerbungen",
        "columns": ["Datum der Bewerbung", "Firma", "Adresse / Kontaktperson / E-Mail / Link zum Online-Formular / Telefon-Nr.",
                    "Stellenbezeichnung", "Pensum", "Zuweisung RAV", "Bewerbung", "Ergebnis der Bewerbung", "Absagegrund"],
        "months": ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
                   "Oktober", "November", "Dezember"],
        "full": "Vollzeit", "part": "Teilzeit {}", "yes": "ja", "spontaneous": " (Spontanbewerbung)",
        "method": {"electronic": "brieflich / elektronisch", "letter": "brieflich / elektronisch",
                   "in-person": "persönlich", "phone": "telefonisch"},
        "result": {"open": "noch offen", "interview": "Vorstellungsgespräch", "rejected": "Absage",
                   "offer": "noch offen", "hired": "Anstellung", "withdrawn": "Absage"},
        "none": "Keine Bewerbungen erfasst.",
    },
    "it": {
        "title": "Prova degli sforzi personali intrapresi per trovare lavoro",
        "name": "Cognome e nome", "month": "Mese e anno", "count": "Numero di domande d'impiego",
        "columns": ["Data della domanda d'impiego", "Ditta", "Indirizzo / Persona consultata / E-mail / Link al modulo online / Telefono n.",
                    "Impiego in qualità di?", "Occup.", "Assegn. dell'URC", "Dom. d'impiego",
                    "Risultato della domanda d'impiego", "Motivo"],
        "months": ["Gennaio", "Febbraio", "Marzo", "Aprile", "Maggio", "Giugno", "Luglio", "Agosto", "Settembre",
                   "Ottobre", "Novembre", "Dicembre"],
        "full": "tempo pieno", "part": "tempo parziale {}", "yes": "sì", "spontaneous": " (candidatura spontanea)",
        "method": {"electronic": "per lettera / elettronica", "letter": "per lettera / elettronica",
                   "in-person": "di persona", "phone": "telefonicamente"},
        "result": {"open": "in sospeso", "interview": "colloquio", "rejected": "non assunzione",
                   "offer": "in sospeso", "hired": "assunzione", "withdrawn": "non assunzione"},
        "none": "Nessuna domanda d'impiego registrata.",
    },
}


def month_label(month, lang):
    y, m = month.split("-")
    return f"{LANGS[lang]['months'][int(m) - 1]} {y}"


def applications(month):
    """(row, latest result, rejection reason) for each application made in month 'YYYY-MM'."""
    all_rows = efforts.read()
    return [(r, *efforts.latest_result(all_rows, r)) for r in all_rows
            if r["type"] in efforts.APPLICATION_TYPES and r["date"].startswith(month)]


def rows_for(month, lang="it"):
    """month = 'YYYY-MM'. Returns report rows (lists matching the language's columns)."""
    L = LANGS[lang]
    out = []
    for r, result, reason in applications(month):
        y, m, d = r["date"].split("-")
        contact = "; ".join(x for x in [r["contact_address"], r["contact_person"], r["contact_email"],
                                        r["link"], r["contact_phone"]] if x)
        title = r["job_title"] + (L["spontaneous"] if r["type"] == "spontaneous-application" else "")
        workload = r["workload"] or "100%"
        workload = L["full"] if workload == "100%" else L["part"].format(workload)
        out.append([f"{d}.{m}.{y}", r["company"], contact, title, workload,
                    L["yes"] if r["rav_assigned"] == "yes" else "", L["method"].get(r["method"], r["method"]),
                    L["result"].get(result, result), reason])
    return out


def as_csv(month, lang="it"):
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(LANGS[lang]["columns"])
    w.writerows(rows_for(month, lang))
    return buf.getvalue()


def as_html(month, name="", lang="it"):
    L = LANGS[lang]
    rows = rows_for(month, lang)
    body = "".join("<tr>" + "".join(f"<td>{html.escape(c)}</td>" for c in r) + "</tr>" for r in rows) \
        or f"<tr><td colspan='{len(L['columns'])}'>{L['none']}</td></tr>"
    head = "".join(f"<th>{html.escape(c)}</th>" for c in L["columns"])
    return f"""<!doctype html><html lang="{lang}"><head><meta charset="utf-8"><title>{L['title']} {month}</title>
<style>body{{font-family:'Liberation Sans',Arial,sans-serif;font-size:9pt}}
table{{border-collapse:collapse;width:100%}}td,th{{border:1px solid #888;padding:3pt;vertical-align:top;text-align:left}}
th{{background:#eee}}</style></head><body>
<h2>{L['title']}</h2>
<p>{L['name']}: {html.escape(name)} &nbsp; {L['month']}: {month_label(month, lang)} &nbsp; {L['count']}: {len(rows)}</p>
<table><tr>{head}</tr>{body}</table></body></html>"""


def insured():
    data = yaml.safe_load(INSURED.read_text(encoding="utf-8")) if INSURED.exists() else None
    return {"name": "", "ahv": "", **(data or {})}


def _short(text, n):
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[:n - 1] + "…"


# Latest result -> the form's "Risultato" checkbox; method -> "Dom. d'impiego" checkbox.
RESULT_BOX = {"open": "in sospeso", "offer": "in sospeso", "interview": "colloquio", "hired": "assunzione",
              "rejected": "non assunzione", "withdrawn": "non assunzione"}
METHOD_BOX = {"electronic": "per lettera", "letter": "per lettera", "": "per lettera",
              "in-person": "di persona", "phone": "telefonica"}


def form_values(month, name="", ahv="", signed_on=None):
    """Field values for each copy of form 716.007 i (14 applications per copy)."""
    apps = applications(month)
    y, m = month.split("-")
    header = {"Cognome e nome": name, "No AVS": ahv, "Mese e anno": f"{LANGS['it']['months'][int(m) - 1].lower()} {y}",
              "Data": (signed_on or dt.date.today()).strftime("%d.%m.%Y")}
    copies = []
    for start in range(0, max(len(apps), 1), ROWS_PER_FORM):
        v = dict(header)
        for i, (r, result, reason) in enumerate(apps[start:start + ROWS_PER_FORM], 1):
            _, mm, dd = r["date"].split("-")
            contact = [r["contact_person"], r["contact_address"], r["contact_email"], r["contact_phone"]]
            contact = "; ".join(_short(x, 60) for x in contact if x)
            ditta = "\n".join(x for x in [r["company"], contact, _short(r["link"], 55)] if x)
            workload = r["workload"] or "100%"
            title = r["job_title"] + (" (candidatura spontanea)" if r["type"] == "spontaneous-application" else "")
            if workload != "100%":
                title += f" ({workload})"
            v[f"giorno-mese {i}"] = f"{dd}.{mm}"
            v[f"Ditta indirizzo_{i}"] = _short(ditta, 170)
            v[f"Impiego in qualità di ?_{i}"] = _short(title, 110)
            boxes = ["a tempo pieno" if workload == "100%" else "a tempo parz (%)", METHOD_BOX.get(r["method"], "per lettera"),
                     RESULT_BOX.get(result or "open", "in sospeso")]
            if r["rav_assigned"] == "yes":
                boxes.append(" dell'URC")
            for b in boxes:
                v[f"{b}_{i}"] = "/Ja"
            if reason and result in ("rejected", "withdrawn"):
                v[f"Motivo_{i}"] = _short(reason, 115)
        copies.append(v)
    return copies, len(apps)


def official_pdf(month, out=None, name=None, ahv=None, signed_on=None):
    """Fill the official form 716.007 i for a month; one copy per 14 applications. Returns (path, count)."""
    from pypdf import PdfReader, PdfWriter
    from pypdf.generic import BooleanObject, NameObject, TextStringObject

    me = insured()
    copies, count = form_values(month, name or me["name"], ahv or me["ahv"], signed_on)
    final = PdfWriter()
    for k, values in enumerate(copies, 1):
        w = PdfWriter(clone_from=PdfReader(FORM_IT))
        for page in w.pages:  # fixed sizes: auto-size makes short entries huge and long ones tiny
            for a in page.get("/Annots") or []:
                a = a.get_object()
                if a.get("/FT") == "/Tx":
                    t = str(a.get("/T"))
                    size = 10 if t in HEADER_FIELDS else 9 if t.startswith("giorno-mese") else 7
                    a[NameObject("/DA")] = TextStringObject(f"/Helv {size} Tf 0 g")
            w.update_page_form_field_values(page, values, auto_regenerate=False)
        if k > 1:  # copies share field names; rename so each keeps its own values
            for page in w.pages:
                for a in page.get("/Annots") or []:
                    a = a.get_object()
                    holder = a if "/T" in a else a.get("/Parent", {}).get_object() if a.get("/Parent") else None
                    if holder is not None and not str(holder["/T"]).endswith(f" #{k}"):
                        holder[NameObject("/T")] = TextStringObject(f"{holder['/T']} #{k}")
        final.append(w)
    if "/AcroForm" in final._root_object:  # let viewers lay out the text (wraps long lines)
        final._root_object["/AcroForm"][NameObject("/NeedAppearances")] = BooleanObject(True)
    REPORTS.mkdir(parents=True, exist_ok=True)
    out = Path(out or REPORTS / f"{month}-716.007-i.pdf")
    with out.open("wb") as f:
        final.write(f)
    return out, count


def write(month, name="", lang="it", pdf=False):
    name = name or insured()["name"]
    REPORTS.mkdir(parents=True, exist_ok=True)
    stem = REPORTS / f"{month}-{lang}"
    stem.with_suffix(".csv").write_text(as_csv(month, lang), encoding="utf-8")
    stem.with_suffix(".html").write_text(as_html(month, name, lang), encoding="utf-8")
    written = [stem.with_suffix(".csv"), stem.with_suffix(".html")]
    if pdf and lang == "it":
        written.append(official_pdf(month, name=name or None)[0])
    elif pdf:
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(REPORTS),
                        str(stem.with_suffix(".html"))], check=True, capture_output=True, timeout=180)
        written.append(stem.with_suffix(".pdf"))
    return written
