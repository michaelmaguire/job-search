"""Emails about an application (acknowledgements, rejections, ...), kept per job.

Saved verbatim as jobs/<id>/emails/<received>-<kind>.md with front matter (kind, received, from).
Pasting one can move the job along: acknowledgement -> 'acknowledged', rejection -> 'rejected' plus a
result row in efforts.csv. Removing or reclassifying an email undoes that. Email text is untrusted data:
stored and shown, never acted on.

Emails can also come from Gmail: gmail_candidates() searches recent mail for replies to open
applications and guesses the job and kind; nothing is saved until you confirm each one. Gmail
messages you dismiss are listed (by Message-ID) in data/emails-ignored.txt so they aren't offered again.
"""
import re

from . import efforts, jobs
from .blocklist import norm_company
from .conversations import me, parse_thread
from .jobs import join_front_matter, split_front_matter
from .paths import DATA

KINDS = {"acknowledgement": "Acknowledgement (received your application)",
         "rejection": "Rejection",
         "other": "Other (just keep it)"}
OPEN_STATUSES = ("applied", "acknowledged", "interviewing", "drafting", "shortlisted")
IGNORED = DATA / "emails-ignored.txt"
# Phrases that mark an email's kind (en, de, it, fr). Rejections are checked first: they often thank you too.
REJECTION_RE = re.compile(
    r"unfortunately|we regret|regret to inform|not (?:be )?(?:moving|move) forward|not to proceed|"
    r"(?:other|another) candidates?|position has (?:now )?been filled|decided not to|"
    r"leider|absage|nicht berücksichtigen|nicht weiter berücksichtig|andere[nr]? kandidat|"
    r"purtroppo|altri candidati|non (?:potremo|possiamo) (?:dare seguito|proseguire)|"
    r"malheureusement|pas (?:pu )?donner (?:une )?suite|autres? candidat", re.I)
ACK_RE = re.compile(
    r"(?:received|receipt of) (?:your )?application|application (?:has been|was) (?:successfully )?(?:received|submitted)|"
    r"thank (?:you|your) for (?:your application|applying)|thanks for (?:your application|applying)|"
    r"eingang ihrer bewerbung|bewerbung (?:ist )?(?:erhalten|eingegangen)|(?:vielen )?dank für (?:ihre|deine) bewerbung|"
    r"abbiamo ricevuto|grazie per (?:la (?:sua|tua) candidatura|aver(?:ci)? inviato)|"
    r"nous avons (?:bien )?reçu|merci (?:pour|de) votre candidature", re.I)
SEARCH_WORDS = ("application", "applying", "candidacy", "bewerbung", "candidatura", "candidature")


def _sender_and_date(text):
    """(name, email, date, body of their newest message) from a pasted email."""
    msgs = [m for m in parse_thread(text, *me()) if m["me"] is not True]
    dated = [m for m in msgs if m["date"]]
    m = dated[-1] if dated else (msgs[-1] if msgs else None)
    if not m:
        return "", "", "", text.strip()
    if not m["email"]:  # a single pasted email often has a "From: Name <x@y>" line
        if f := re.search(r"^\s*(?:from|von|da|de)\s*:\s*(.+)$", text, re.I | re.M):
            from .conversations import _sender
            m["name"], m["email"] = _sender(f.group(1))
    return m["name"], m["email"], m["date"], m["body"] or text.strip()


def guess_jobs(text):
    """Applications this email is probably about, best first: [(score, job)]."""
    _, email, _, _ = _sender_and_date(text)
    low = " ".join(text.lower().split())
    domain = email.split("@")[-1].split(".")[-2] if "@" in email and email.count(".") else ""
    out = []
    for j in jobs.all_jobs():
        if j.get("status") not in OPEN_STATUSES:
            continue
        score = 0
        company = norm_company(j.get("company"))
        if company and company in norm_company(low):
            score += 5
        first = company.split(" ")[0] if company else ""
        if domain and first and (domain in first or first in domain):
            score += 4
        if domain and domain in str(j.get("url", "")).lower():
            score += 3
        if not score:  # shared title words alone ("Senior Software Engineer") say nothing about the company
            continue
        words = {w for w in re.findall(r"[a-z+#]{4,}", str(j.get("title", "")).lower())}
        if words:
            score += round(3 * sum(w in low for w in words) / len(words))
        score += {"applied": 2, "acknowledged": 1}.get(j.get("status"), 0)
        if score >= 3:
            out.append((score, j))
    return sorted(out, key=lambda x: (-x[0], x[1].get("status_changed", "")))


def guess_kind(text):
    """'rejection', 'acknowledgement' or 'other' from the wording of an email."""
    if REJECTION_RE.search(text or ""):
        return "rejection"
    return "acknowledgement" if ACK_RE.search(text or "") else "other"


def as_text(msg):
    """A Gmail message (from gmail.messages(full=True)) as the text save() keeps: header lines, then the body."""
    return f"From: {msg['from']}\nDate: {msg['date']}\nSubject: {msg['subject']}\n\n{msg['body']}".strip()


def ignored():
    return set(IGNORED.read_text(encoding="utf-8").split()) if IGNORED.exists() else set()


def ignore(message_id):
    """Don't offer this Gmail message again."""
    message_id = "".join((message_id or "").split())
    if message_id and message_id not in ignored():
        with IGNORED.open("a", encoding="utf-8") as f:
            f.write(message_id + "\n")


def saved_message_ids():
    out = set()
    for j in jobs.all_jobs():
        folder = jobs.job_dir(j["id"]) / "emails"
        for p in folder.glob("*.md") if folder.exists() else []:
            if mid := split_front_matter(p.read_text(encoding="utf-8"))[0].get("message_id"):
                out.add(mid)
    return out


def _address(sender):
    from .conversations import _sender
    return _sender(sender or "")[1]


def saved_senders():
    """{(sender address, received date, kind)} of every saved email - pasted ones have no message_id."""
    out = set()
    for j in jobs.all_jobs():
        for m in emails(j["id"]):
            if (addr := _address(m.get("from"))) and m.get("received"):
                out.add((addr, str(m["received"]), m.get("kind", "")))
    return out


def _already_saved(addr, received, kind, saved):
    """True if an email of this kind from `addr` was saved within a day of `received` (time zones shift
    the date). Kind 'other' matches any kind, so an acknowledgement doesn't hide a rejection sent next day."""
    import datetime as dt
    try:
        day = dt.date.fromisoformat(received)
    except ValueError:
        return False
    days = {str(day + dt.timedelta(days=d)) for d in (-1, 0, 1)}
    return any(a == addr and r in days and kind in (k, "other") for a, r, k in saved)


def gmail_query(days=30):
    """Gmail search for mail from others in the last `days` days that names an open application's
    company or talks about an application."""
    companies = {norm_company(j.get("company")) for j in jobs.all_jobs() if j.get("status") in OPEN_STATUSES}
    terms = [f'"{c}"' for c in sorted(companies) if len(c) >= 3] + list(SEARCH_WORDS)
    return f"newer_than:{int(days)}d -from:me -in:sent -in:drafts -in:chats ({' OR '.join(terms)})"


def gmail_candidates(days=30, limit=50, fetch=None):
    """Recent Gmail messages that look like replies to open applications, not yet saved or dismissed:
    [{message_id, date, from, subject, snippet, text, kind, guesses: [(score, job)]}], newest first.
    `fetch` is gmail.messages (passed in by tests)."""
    if fetch is None:
        from .gmail import messages as fetch
    skip, saved, mine = ignored() | saved_message_ids(), saved_senders(), {e.lower() for e in me()[1]}
    out = []
    for m in fetch(gmail_query(days), limit, full=True):
        addr = _address(m["from"])
        if m["message_id"] in skip or addr in mine:
            continue
        text = as_text(m)
        kind = guess_kind(text)
        if _already_saved(addr, _sender_and_date(text)[2], kind, saved):
            continue
        guesses = guess_jobs(text)
        if kind == "other" and not (guesses and guesses[0][0] >= 7):  # a job alert or newsletter, most likely
            continue
        snippet = " ".join(_sender_and_date(text)[3].split())
        out.append({"message_id": m["message_id"], "date": m["date"], "from": m["from"], "subject": m["subject"],
                    "snippet": snippet[:240] + ("…" if len(snippet) > 240 else ""),
                    "text": text, "kind": kind, "guesses": guesses})
    return out


def emails(job_id):
    folder = jobs.job_dir(job_id) / "emails"
    out = []
    for p in sorted(folder.glob("*.md")) if folder.exists() else []:
        meta, body = split_front_matter(p.read_text(encoding="utf-8"))
        out.append({**meta, "file": p.name, "text": body.strip()})
    return out


def save(job_id, text, kind="acknowledgement", reason="", message_id=""):
    """Store the email and update the job. Returns (summary, warnings)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    text = (text or "").replace("\r\n", "\n").strip()
    if not text:
        raise ValueError("paste the email first")
    j = jobs.load(job_id)
    name, email, received, body = _sender_and_date(text)
    received = received or jobs.today()
    folder = j["dir"] / "emails"
    folder.mkdir(exist_ok=True)
    path, n = folder / f"{received}-{kind}.md", 2
    while path.exists():
        if split_front_matter(path.read_text(encoding="utf-8"))[1].strip() == text:
            return f"this email was already saved for {j['company']}", []
        path, n = folder / f"{received}-{kind}-{n}.md", n + 1
    sender = f"{name} <{email}>" if name and email else (name or email)
    meta = {"kind": kind, "received": received, "from": sender}
    if message_id := "".join((message_id or "").split()):
        if message_id in saved_message_ids():
            return "this email was already saved", []
        meta["message_id"] = message_id
    path.write_text(join_front_matter(meta, "\n" + text + "\n"), encoding="utf-8")

    warnings, status = [], j.get("status")
    if kind == "acknowledgement":
        if status in ("new", "shortlisted", "drafting", "applied"):
            jobs.set_status(j["id"], "acknowledged")
        efforts.ensure_application(j["id"], date=received if status not in ("applied", "acknowledged") else None)
        return f"acknowledgement from {j['company']} saved ({received}); job is now acknowledged", warnings
    if kind == "rejection":
        efforts.ensure_application(j["id"])
        app = next((r for r in reversed(efforts.read()) if r["job_id"] == j["id"] and r["type"] in efforts.APPLICATION_TYPES), {})
        reason = (reason or "").strip() or " ".join(body.split())[:250]
        errors = efforts.add({"date": received, "type": "result", "company": app.get("company") or j.get("company", ""),
                              "job_id": j["id"], "job_title": app.get("job_title") or j.get("title", ""), "result": "rejected",
                              "rejection_reason": reason, "contact_person": app.get("contact_person", ""),
                              "contact_email": email or app.get("contact_email", ""),
                              "notes": f"from the email saved as emails/{path.name}"})
        warnings += errors
        return f"rejection from {j['company']} saved ({received}); result logged in Efforts", warnings
    return f"email saved for {j['company']} ({received})", warnings


def _file(job_id, name):
    """Path of a saved email, only if `name` is one of this job's emails (no paths from the form)."""
    folder = jobs.job_dir(job_id) / "emails"
    if name not in ({p.name for p in folder.glob("*.md")} if folder.exists() else set()):
        raise ValueError(f"no saved email {name!r} for this job")
    return folder / name


def _status_from_records(job_id):
    """The status the job's remaining emails and efforts rows support (None: nothing to go on)."""
    kinds = {m.get("kind") for m in emails(job_id)}
    if "rejection" in kinds:
        return "rejected"
    status = None
    for r in sorted((r for r in efforts.read() if r["job_id"] == job_id), key=lambda r: r["date"]):
        status = efforts.job_status_after(r) or status
    if status in (None, "applied") and "acknowledgement" in kinds:
        return "acknowledged"
    return status


def remove(job_id, name):
    """Delete a saved email, its logged rejection result, and the status it set. Returns a summary."""
    j = jobs.load(job_id)
    path = _file(j["id"], name)
    kind = split_front_matter(path.read_text(encoding="utf-8"))[0].get("kind")
    path.unlink()
    note = f"from the email saved as emails/{name}"
    rows = efforts.read()
    kept = [r for r in rows if not (r["job_id"] == j["id"] and r["type"] == "result" and r["notes"] == note)]
    if len(kept) != len(rows):
        efforts.write(kept)
    summary = f"{kind or 'email'} {name} removed from {j['company']}"
    if len(kept) != len(rows):
        summary += "; its result row was removed from Efforts"
    if j.get("status") in ("acknowledged", "rejected"):
        status = _status_from_records(j["id"])
        if status and status != j["status"]:
            jobs.set_status(j["id"], status)
            summary += f"; job is back to {status}"
    return summary


def reclassify(job_id, name, kind, reason=""):
    """Change a saved email's kind (e.g. acknowledgement -> rejection), redoing its effects. Returns (summary, warnings)."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    path = _file(job_id, name)
    meta, text = split_front_matter(path.read_text(encoding="utf-8"))
    if meta.get("kind") == kind:
        return f"{name} is already {kind}", []
    remove(job_id, name)
    return save(job_id, text.strip(), kind, reason, meta.get("message_id", ""))
