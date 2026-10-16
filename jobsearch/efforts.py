"""efforts.csv: one row per job-search effort (see data/README.md for the columns)."""
import csv

from .paths import EFFORTS

FIELDS = ["date", "type", "company", "job_id", "job_title", "workload", "method", "rav_assigned",
          "contact_person", "contact_email", "contact_phone", "contact_address", "link",
          "result", "rejection_reason", "interview_with", "interviewer_role", "interview_mode",
          "interview_time", "documents", "notes"]
TYPES = ["application", "spontaneous-application", "recruiter-contact", "networking",
         "interview", "follow-up", "result"]
METHODS = ["electronic", "letter", "in-person", "phone"]
RESULTS = ["open", "interview", "rejected", "offer", "hired", "withdrawn"]
APPLICATION_TYPES = {"application", "spontaneous-application"}

# Logging an effort against a job moves the job's status along.
STATUS_FOR = {"application": "applied", "spontaneous-application": "applied", "interview": "interviewing"}
STATUS_FOR_RESULT = {"rejected": "rejected", "offer": "offer", "hired": "offer", "withdrawn": "withdrawn",
                     "interview": "interviewing"}


def read():
    if not EFFORTS.exists():
        return []
    with EFFORTS.open(newline="", encoding="utf-8") as f:
        return [{k: (r.get(k) or "") for k in FIELDS} for r in csv.DictReader(f)]


def write(rows):
    rows = sorted(rows, key=lambda r: r["date"])
    with EFFORTS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def validate(row):
    errors = []
    if not row.get("date"):
        errors.append("date is required")
    if row.get("type") not in TYPES:
        errors.append(f"type must be one of {TYPES}")
    if not row.get("company"):
        errors.append("company is required")
    if row.get("method") and row["method"] not in METHODS:
        errors.append(f"method must be one of {METHODS}")
    if row.get("result") and row["result"] not in RESULTS:
        errors.append(f"result must be one of {RESULTS}")
    return errors


def job_status_after(row):
    if row.get("result") in STATUS_FOR_RESULT:
        return STATUS_FOR_RESULT[row["result"]]
    return STATUS_FOR.get(row.get("type"))


def add(row):
    """Validate, append, and advance the linked job's status. Returns list of errors."""
    from . import jobs
    row = {k: (row.get(k) or "").strip() for k in FIELDS}
    errors = validate(row)
    if errors:
        return errors
    write(read() + [row])
    status = job_status_after(row)
    if row["job_id"] and status:
        try:
            jobs.set_status(row["job_id"], status)
        except SystemExit:
            pass
    return []


def latest_result(rows, app):
    """Latest result recorded for the same job (or company + title) on or after the application."""
    result, reason = app["result"] or "open", app["rejection_reason"]
    for r in rows:
        same = (app["job_id"] and r["job_id"] == app["job_id"]) or \
               (not app["job_id"] and r["company"] == app["company"] and r["job_title"] == app["job_title"])
        if same and r["date"] >= app["date"] and r["result"]:
            result, reason = r["result"], r["rejection_reason"] or reason
    return result, reason


def ensure_application(job_id, date=None):
    """When a job is marked applied: log its application row unless one already exists.
    Returns the new row, or None if the job already had one. Edit date/method afterwards if needed."""
    from . import jobs
    if any(r["job_id"] == job_id and r["type"] in APPLICATION_TYPES for r in read()):
        return None
    j = jobs.load(job_id)
    d = j["dir"]
    letters = sorted(d.glob("*-CoverLetter-*.pdf"), key=lambda f: f.stat().st_mtime)
    docs = [letters[-1].name] if letters else []
    try:
        from .gmail import cv_path
        cv = cv_path(d)
        if cv:
            docs.append(cv.name)
    except Exception:
        pass
    row = dict.fromkeys(FIELDS, "")
    row.update(date=date or jobs.today(), type="application", company=j.get("company", ""), job_id=job_id,
               job_title=j.get("title", ""), method="electronic", link=j.get("url", ""), result="open",
               documents="; ".join(docs), notes="logged automatically when the job was marked applied")
    if j.get("via"):  # through a recruiter: "Akros via Gulenko", recruiter as the contact
        try:
            from .conversations import load as load_conversation
            c = load_conversation(j["via"])
            row.update(company=f"{row['company']} via {c.get('agency') or c.get('contact')}",
                       contact_person=c.get("contact", ""), contact_email=c.get("email", ""), contact_phone=c.get("phone", ""),
                       notes=f"via recruiter {c.get('contact', '')} (conversation {j['via']}); logged when marked applied")
        except FileNotFoundError:
            pass
    write(read() + [row])
    return row
