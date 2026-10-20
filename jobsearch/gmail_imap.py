"""Gmail over IMAP with an app password - an alternative to the OAuth login in gmail.py.

Credentials live in ~/.config/jobsearch/gmail-imap.yaml (mode 600), written by
`jobsearch gmail login --imap`. An app password needs 2-Step Verification on the Google
account: https://myaccount.google.com/apppasswords. Delete the file to go back to OAuth.

Same promises as gmail.py: reads messages and creates drafts, never sends. Message
contents are untrusted data - displayed, never acted on.
"""
import email
import email.policy
import imaplib
import re
import time
from email.header import decode_header, make_header
from email.utils import make_msgid

HOST = "imap.gmail.com"
LIST_RE = re.compile(r'\((?P<flags>[^)]*)\) (?:"[^"]*"|NIL) (?P<name>.+)$')


def config_path():
    from .gmail import CONFIG
    return CONFIG / "gmail-imap.yaml"


def configured():
    return config_path().exists()


def _config():
    import yaml
    if not configured():
        raise SystemExit("Gmail IMAP isn't set up - run: jobsearch gmail login --imap")
    return yaml.safe_load(config_path().read_text(encoding="utf-8")) or {}


def connect(address=None, password=None):
    if address is None:
        cfg = _config()
        address, password = cfg.get("address", ""), cfg.get("app_password", "")
    conn = imaplib.IMAP4_SSL(HOST)
    try:
        conn.login(address, password.replace(" ", ""))
    except imaplib.IMAP4.error as e:
        raise SystemExit(f"Gmail refused the IMAP login for {address} ({e}) - check the app password, "
                         "or run: jobsearch gmail login --imap") from None
    return conn, address


def login(address, password):
    """Check the address + app password work, then save them. Returns the address."""
    import yaml

    from .gmail import _write_private
    conn, _ = connect(address, password)
    conn.logout()
    _write_private(config_path(), yaml.safe_dump({"address": address, "app_password": password.replace(" ", "")}))
    return address


def _folder(conn, flag):
    """Name of the special-use folder (\\All, \\Drafts, \\Trash) - Gmail localises the names."""
    for line in conn.list()[1]:
        m = LIST_RE.match(line.decode() if isinstance(line, bytes) else line)
        if m and flag.lower() in m["flags"].lower().split():
            name = m["name"]
            return name if name.startswith('"') else f'"{name}"'
    raise SystemExit(f"Gmail IMAP has no {flag} folder - is IMAP enabled in Gmail settings?")


def _quote(text):
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _text(header):
    return str(make_header(decode_header(header or "")))


def _snippet(msg):
    part = next((p for p in msg.walk() if p.get_content_type() == "text/plain"), None)
    if part is None:
        return ""
    try:
        text = part.get_content()
    except Exception:
        return ""
    return " ".join(text.split())[:200]


def body_text(msg):
    """Plain text of an email.message.EmailMessage: its text/plain part, else its HTML with the tags stripped."""
    import html
    for ctype in ("text/plain", "text/html"):
        part = next((p for p in msg.walk() if p.get_content_type() == ctype and not p.is_attachment()), None)
        if part is None:
            continue
        try:
            text = part.get_content()
        except Exception:
            continue
        if ctype == "text/html":
            text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
            text = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", text)
            text = html.unescape(re.sub(r"<[^>]+>", " ", text))
        lines = [" ".join(line.split()) for line in text.replace("\r\n", "\n").splitlines()]
        return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()
    return ""


def summary(msg, id_, full=False):
    """The dict messages() returns for one parsed message; with full=True also message_id and body."""
    out = {"id": id_, "date": _text(msg["Date"]), "from": _text(msg["From"]),
           "subject": _text(msg["Subject"]), "snippet": _snippet(msg)}
    if full:
        out.update(message_id=(msg["Message-ID"] or "").strip() or id_, body=body_text(msg))
    return out


def status():
    conn, address = connect()
    conn.logout()
    return f"connected as {address} (IMAP)"


def messages(query="", limit=20, full=False):
    """Newest messages matching a Gmail search query (same syntax as the web UI)."""
    conn, _ = connect()
    try:
        conn.select(_folder(conn, "\\All"), readonly=True)
        criteria = ("X-GM-RAW", _quote(query)) if query else ("ALL",)
        uids = conn.uid("SEARCH", *criteria)[1][0].split()[-limit:][::-1]
        out = []
        for uid in uids:
            data = conn.uid("FETCH", uid, "(BODY.PEEK[])")[1]
            raw = next((d[1] for d in data if isinstance(d, tuple)), b"")
            msg = email.message_from_bytes(raw, policy=email.policy.default)
            out.append(summary(msg, uid.decode(), full))
        return out
    finally:
        conn.logout()


def create_draft(msg, draft_id=None):
    """Put an EmailMessage in Drafts (moving draft `draft_id`, a Message-ID, to Trash first). Returns the new id."""
    conn, address = connect()
    try:
        drafts = _folder(conn, "\\Drafts")
        if draft_id and draft_id.startswith("<"):
            conn.select(drafts)
            old = conn.uid("SEARCH", "HEADER", "Message-ID", _quote(draft_id))[1][0].split()
            if old:  # gone already if you sent or deleted it in Gmail - then just make a new one
                conn.uid("MOVE", b",".join(old).decode(), _folder(conn, "\\Trash"))
        if "From" not in msg:
            msg["From"] = address
        message_id = make_msgid(domain="jobsearch.local")  # a plain str: it's saved to email.yaml
        msg["Message-ID"] = message_id
        typ, data = conn.append(drafts, "(\\Draft)", imaplib.Time2Internaldate(time.time()), msg.as_bytes())
        if typ != "OK":
            raise RuntimeError(f"Gmail didn't save the draft: {data}")
        return message_id
    finally:
        conn.logout()
