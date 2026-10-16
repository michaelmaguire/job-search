"""Gmail: read messages and create drafts (never sends).

Credentials live outside both repos, in ~/.config/jobsearch/ (override with JOBSEARCH_CONFIG):
  gmail-client.json   OAuth "Desktop app" client downloaded from Google Cloud (you provide it)
  gmail-token.json    written by `jobsearch gmail login` (mode 600)
or, instead of OAuth, IMAP with an app password (see gmail_imap.py):
  gmail-imap.yaml     written by `jobsearch gmail login --imap` (mode 600); used whenever it exists

Scopes: gmail.readonly (read) and gmail.compose (create drafts). This module has no
send function; drafts are reviewed and sent by you in Gmail. Message contents are
untrusted data - they are displayed, never acted on.
"""
import base64
import mimetypes
import os
from email.message import EmailMessage
from pathlib import Path

from . import gmail_imap

CONFIG = Path(os.environ.get("JOBSEARCH_CONFIG", Path.home() / ".config" / "jobsearch"))
CLIENT = CONFIG / "gmail-client.json"
TOKEN = CONFIG / "gmail-token.json"
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly", "https://www.googleapis.com/auth/gmail.compose"]


def _write_private(path, text):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)


def credentials(interactive=False):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _write_private(TOKEN, creds.to_json())
            return creds
        except Exception:
            if not interactive:
                raise SystemExit("Google no longer accepts the saved Gmail login (expired or revoked) - "
                                 "run: jobsearch gmail login")
    if not interactive:
        raise SystemExit("not logged in to Gmail - run: jobsearch gmail login")
    if not CLIENT.exists():
        raise SystemExit(f"missing {CLIENT} - download the OAuth Desktop client JSON from Google Cloud")
    from google_auth_oauthlib.flow import InstalledAppFlow
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT), SCOPES)
    forget_status()
    creds = flow.run_local_server(port=0, open_browser=True,
                                  authorization_prompt_message="Opening the browser to sign in to Gmail:\n{url}\n")
    _write_private(TOKEN, creds.to_json())
    return creds


def service(interactive=False):
    from googleapiclient.discovery import build
    return build("gmail", "v1", credentials=credentials(interactive), cache_discovery=False)


def login():
    """Sign in in the browser; returns the account address."""
    return service(interactive=True).users().getProfile(userId="me").execute()["emailAddress"]


def messages(query="", limit=20):
    """Recent messages matching a Gmail search query: id, date, from, subject, snippet."""
    if gmail_imap.configured():
        return gmail_imap.messages(query, limit)
    svc = service()
    ids = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
    out = []
    for m in ids:
        msg = svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                         metadataHeaders=["From", "Subject", "Date"]).execute()
        h = {x["name"].lower(): x["value"] for x in msg["payload"].get("headers", [])}
        out.append({"id": m["id"], "date": h.get("date", ""), "from": h.get("from", ""),
                    "subject": h.get("subject", ""), "snippet": msg.get("snippet", "")})
    return out


def _message(to, subject, body, attachments):
    msg = EmailMessage()
    if to:
        msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    for p in map(Path, attachments):
        ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
        maintype, subtype = ctype.split("/", 1)
        msg.add_attachment(p.read_bytes(), maintype=maintype, subtype=subtype, filename=p.name)
    return msg


def create_draft(to, subject, body, attachments=(), draft_id=None):
    """Create (not send) a draft, or replace the content of draft `draft_id`. Returns the draft id."""
    if gmail_imap.configured():
        return gmail_imap.create_draft(_message(to, subject, body, attachments), draft_id)
    from googleapiclient.errors import HttpError
    drafts = service().users().drafts()
    message = {"message": {"raw": base64.urlsafe_b64encode(_message(to, subject, body, attachments).as_bytes()).decode()}}
    if draft_id and draft_id.startswith("<"):  # a Message-ID from an IMAP draft, not an OAuth draft id
        draft_id = None
    if draft_id:
        try:
            return drafts.update(userId="me", id=draft_id, body={"id": draft_id, **message}).execute()["id"]
        except HttpError as e:
            if e.resp.status != 404:  # 404: you sent or deleted it in Gmail - make a new one
                raise
    return drafts.create(userId="me", body=message).execute()["id"]


# ---------------------------------------------------------------- application email for a job
# jobs/<id>/email.yaml keeps the fields you edit in the web app and the Gmail draft id, so
# saving again updates the same Gmail draft instead of creating another.

_status_cache = {"at": 0.0, "value": None}


def status(max_age=60):
    """(ok, message) - actually asks Gmail, since Google can revoke a saved login at any time
    (e.g. while a new account is being verified). Cached for max_age seconds."""
    import time
    if not TOKEN.exists() and not gmail_imap.configured():
        return False, "Gmail isn't connected - run: jobsearch gmail login (or login --imap)"
    if _status_cache["value"] and time.time() - _status_cache["at"] < max_age:
        return _status_cache["value"]
    try:
        if gmail_imap.configured():
            value = (True, gmail_imap.status())
        else:
            address = service().users().getProfile(userId="me").execute()["emailAddress"]
            value = (True, f"connected as {address}")
    except SystemExit as e:  # expired and not renewable
        value = (False, f"{e}")
    except Exception as e:
        reason = str(e)
        if "invalid_grant" in reason or "401" in reason or "revoked" in reason.lower():
            value = (False, "Google revoked the Gmail login - run: jobsearch gmail login")
        else:
            value = (False, f"can't reach Gmail right now ({reason[:120]})")
    _status_cache.update(at=time.time(), value=value)
    return value


def forget_status():
    _status_cache.update(at=0.0, value=None)


def _sender(d):
    from .check import load_rules
    from .jobs import split_front_matter
    draft = d / "letter.draft.md"
    meta = split_front_matter(draft.read_text(encoding="utf-8"))[0] if draft.exists() else {}
    return meta, load_rules()["senders"][meta.get("profile", "ch")]


def cv_path(job):
    """The CV of the letter's sender profile (letter-rules.yaml `cv:`), or None."""
    from .jobs import job_dir
    from .paths import DATA
    _, sender = _sender(job_dir(job))
    return DATA / sender["cv"] if sender.get("cv") else None


def attachments(job, include_letter=True, extra=()):
    """(paths, problems): the latest rendered letter PDF and the sender profile's CV."""
    from .jobs import job_dir
    from .paths import DATA
    d = job_dir(job)
    _, sender = _sender(d)
    files, problems = [], []
    if include_letter:
        pdfs = sorted(d.glob("*-CoverLetter-*.pdf"), key=lambda p: p.stat().st_mtime)
        if pdfs:
            files.append(pdfs[-1])
        else:
            problems.append("no rendered letter yet - use Check & render first")
    if sender.get("cv"):
        files.append(DATA / sender["cv"])
    files += [Path(p) for p in extra]
    problems += [f"attachment not found: {p}" for p in files if not p.exists()]
    return [p for p in files if p.exists()], problems


def email(job):
    """The application email for a job: saved fields from email.yaml over the defaults."""
    import yaml

    from .jobs import job_dir, load
    d = job_dir(job)
    j = load(d)
    meta, sender = _sender(d)
    files, _ = attachments(d)
    enclosed = " and ".join(n for n, f in [("cover letter", any("-CoverLetter-" in p.name for p in files)),
                                             ("CV", bool(sender.get("cv")))] if f) or "application"
    out = {"to": "", "subject": meta.get("subject") or f"Application for {j.get('title', '')}",
           "body": (f"Dear Hiring Team,\n\nPlease find attached my {enclosed} for the position of "
                    f"{j.get('title', '')}.\n\nKind regards,\n\n{sender['name']}\n{sender['phone']}\n"),
           "gmail_draft_id": "", "gmail_saved": ""}
    f = d / "email.yaml"
    if f.exists():
        out.update({k: v for k, v in (yaml.safe_load(f.read_text(encoding="utf-8")) or {}).items() if v is not None})
    return out


def save_email(job, **fields):
    import yaml

    from .jobs import job_dir
    d = job_dir(job)
    data = {**email(d), **{k: v for k, v in fields.items() if v is not None}}
    (d / "email.yaml").write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return data


def save_to_gmail(job, to, subject, body, include_letter=True, extra=()):
    """Save the email fields and put them in Gmail Drafts (updating this job's earlier draft if any).
    Returns (draft id, attachment names). Never sends."""
    import datetime as dt

    from .jobs import job_dir
    d = job_dir(job)
    files, problems = attachments(d, include_letter, extra)
    if problems:
        raise RuntimeError("; ".join(problems))
    old = email(d).get("gmail_draft_id") or None
    draft_id = create_draft(to.strip(), subject, body.replace("\r\n", "\n"), files, draft_id=old)
    save_email(d, to=to.strip(), subject=subject, body=body.replace("\r\n", "\n"), gmail_draft_id=draft_id,
               gmail_saved=dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    return draft_id, [p.name for p in files]
