"""Recruiter conversations: paste an email thread, keep it, split it into messages, link jobs to it.

Stored as data/conversations/<first-date>-<agency>-<contact>.md: YAML front matter (contact, email,
agency, phone, status, jobs, notes) and every pasted thread kept verbatim between markers. Messages
are parsed from those pastes on load and de-duplicated, so pasting a longer version of the same
thread later just adds the new messages. Email text is untrusted data: shown, never acted on.
"""
import datetime as dt
import hashlib
import re

from .jobs import join_front_matter, slugify, split_front_matter, today
from .paths import DATA

DIR = DATA / "conversations"
PASTE_RE = re.compile(r"<!-- pasted (?P<at>[^>]*?) -->\n(?P<text>.*?)\n<!-- end of paste -->", re.S)
GENERIC_DOMAINS = {"gmail", "googlemail", "outlook", "hotmail", "live", "yahoo", "protonmail", "proton", "pm",
                   "icloud", "me", "gmx", "bluewin", "hispeed", "sunrise", "web", "aol", "mail"}
MONTHS = {m: i for i, names in enumerate([
    "jan january januar gennaio janvier", "feb february februar febbraio fevrier février",
    "mar march märz maerz marzo mars", "apr april aprile avril", "may mai maggio",
    "jun june juni giugno juin", "jul july juli luglio juillet", "aug august agosto aout août",
    "sep sept september settembre septembre", "oct october oktober ottobre octobre okt ott",
    "nov november novembre", "dec december dezember dicembre decembre décembre dez dic"], 1) for m in names.split()}
HEADER_RE = re.compile(r"^\s*(?:on|am|il|le)\s+(?P<inner>.+?)\s*(?:wrote|schrieb|ha scritto|a écrit)\s*:\s*$", re.I | re.M)
OUTLOOK_RE = re.compile(r"^\s*(?:from|von|da|de)\s*:\s*(?P<who>.+)\n(?:.*\n){0,3}?\s*(?:sent|date|gesendet|datum|inviato|data|envoyé)\s*:\s*(?P<when>.+)$",
                        re.I | re.M)
EMAIL_RE = re.compile(r"[<(\[]?\s*(?:mailto:)?([\w.+-]+@[\w-]+(?:\.[\w-]+)+)\s*[>)\]]?")
NOISE = re.compile(r"^\s*(-{3,}\s*(original message|forwarded message|ursprüngliche nachricht)\s*-{3,}|"
                   r"sent from (proton mail|my iphone|my android|outlook)[^\n]*|von meinem iphone gesendet)\s*$", re.I | re.M)
URL_RE = re.compile(r"https?://[^\s<>()\"']+")


def parse_when(text):
    """('2026-10-15', '13:36') from the date part of an email header; ('', '') if not understood."""
    s = text.lower()
    date = ""
    m = re.search(r"([a-zäéû]{3,})\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})", s)
    if m and m.group(1) in MONTHS:
        date = f"{int(m.group(3)):04d}-{MONTHS[m.group(1)]:02d}-{int(m.group(2)):02d}"
    if not date and (m := re.search(r"(\d{1,2})(?:st|nd|rd|th)?\.?\s+([a-zäéû]{3,})\.?\s+(\d{4})", s)) and m.group(2) in MONTHS:
        date = f"{int(m.group(3)):04d}-{MONTHS[m.group(2)]:02d}-{int(m.group(1)):02d}"
    if not date and (m := re.search(r"(\d{4})-(\d{2})-(\d{2})", s)):
        date = m.group(0)
    if not date and (m := re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{2,4})\b", s)):  # US m/d/y (Proton, Outlook en-US)
        y = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        date = f"{y:04d}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
    if not date and (m := re.search(r"\b(\d{1,2})\.(\d{1,2})\.(\d{2,4})\b", s)):  # d.m.y
        y = int(m.group(3)) + (2000 if len(m.group(3)) == 2 else 0)
        date = f"{y:04d}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    try:
        dt.date.fromisoformat(date)
    except ValueError:
        date = ""
    time = ""
    if m := re.search(r"\b(\d{1,2}):(\d{2})\s*([ap]\.?m\.?)?", s):
        h = int(m.group(1)) % 12 + (12 if m.group(3) and m.group(3)[0] == "p" else 0) if m.group(3) else int(m.group(1))
        time = f"{h:02d}:{m.group(2)}"
    return date, time


def _sender(text):
    """(name, email) from the 'who' part of a header."""
    m = EMAIL_RE.search(text)
    email = m.group(1).lower() if m else ""
    pre = text[:m.start()] if m else text
    name = re.split(r",|\d{1,2}:\d{2}(?:\s*[ap]\.?m\.?)?|\bat\b|\bum\b|\balle\b", pre, flags=re.I)[-1]
    return name.strip(" ,:\"'"), email


def _clean(body):
    body = NOISE.sub("", body)
    lines = [line for line in body.splitlines() if not line.lstrip().startswith(">")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def parse_thread(text, my_names=(), my_emails=()):
    """Messages in a pasted thread, oldest first: {date, time, name, email, me, body}."""
    text = (text or "").replace("\r\n", "\n")
    heads = []
    for m in HEADER_RE.finditer(text):
        name, email = _sender(m.group("inner"))
        date, time = parse_when(m.group("inner"))
        heads.append((m.start(), m.end(), name, email, date, time))
    for m in OUTLOOK_RE.finditer(text):
        name, email = _sender(m.group("who"))
        date, time = parse_when(m.group("when"))
        end = text.find("\n\n", m.end())
        heads.append((m.start(), end if end > 0 else m.end(), name, email, date, time))
    heads.sort()
    mine = {n.lower() for n in my_names}
    my_addrs = {e.lower() for e in my_emails}
    out = []
    top = _clean(text[:heads[0][0]] if heads else text)
    if top:  # reply text above the first quoted header: its sender/date aren't in the paste
        out.append({"date": "", "time": "", "name": "", "email": "", "me": None, "body": top})
    for i, (_, end, name, email, date, time) in enumerate(heads):
        body = _clean(text[end:heads[i + 1][0] if i + 1 < len(heads) else len(text)])
        out.append({"date": date, "time": time, "name": name, "email": email, "body": body,
                    "me": any(n and n in name.lower() for n in mine) or (bool(email) and email.lower() in my_addrs)})
    out.sort(key=lambda m: (m["date"] or "9999", m["time"]))
    return out


def _key(msg):
    return hashlib.sha1(f"{msg['email']}|{msg['date']}|{msg['time']}|{' '.join(msg['body'].split())[:200]}".encode()).hexdigest()


def agency_from(email):
    domain = (email or "").split("@")[-1].split(".")
    label = domain[-2] if len(domain) >= 2 else ""
    return "" if label in GENERIC_DOMAINS else label.capitalize()


def me():
    """(names, emails) that mark a message as yours: the senders in letter-rules.yaml plus its `my_emails`."""
    from .check import load_rules
    try:
        rules = load_rules()
    except Exception:
        return [], []
    senders = (rules.get("senders") or {}).values()
    emails = [s["email"] for s in senders if s.get("email") and "{" not in s["email"]] + list(rules.get("my_emails") or [])
    return [s["name"] for s in senders if s.get("name")], emails


def load(conv_id):
    p = DIR / f"{conv_id}.md"
    if not p.exists():
        raise FileNotFoundError(conv_id)
    meta, body = split_front_matter(p.read_text(encoding="utf-8"))
    pastes = [(m.group("at"), m.group("text")) for m in PASTE_RE.finditer(body)]
    seen, messages = set(), []
    for _, text in pastes:
        for msg in parse_thread(text, *me()):
            if (k := _key(msg)) not in seen:
                seen.add(k)
                messages.append(msg)
    messages.sort(key=lambda m: (m["date"] or "9999", m["time"]))
    return {"id": conv_id, **meta, "jobs": list(meta.get("jobs") or []), "messages": messages, "pastes": pastes,
            "links": sorted({u.rstrip(".,;") for _, t in pastes for u in URL_RE.findall(t)})}


def all_conversations():
    if not DIR.exists():
        return []
    convs = []
    for p in DIR.glob("*.md"):
        c = load(p.stem)
        dates = [m["date"] for m in c["messages"] if m["date"]]
        c["first"], c["last"] = (min(dates), max(dates)) if dates else ("", "")
        convs.append(c)
    return sorted(convs, key=lambda c: c["last"] or c["id"], reverse=True)


def _write(conv_id, meta, pastes):
    DIR.mkdir(parents=True, exist_ok=True)
    body = "\n" + "\n\n".join(f"<!-- pasted {at} -->\n{text}\n<!-- end of paste -->" for at, text in pastes) + "\n"
    (DIR / f"{conv_id}.md").write_text(join_front_matter(meta, body), encoding="utf-8")


def save_paste(text, agency=""):
    """Store a pasted thread, merging it into the conversation with the same contact if there is one.
    Returns (conv_id, total messages, new messages)."""
    text = (text or "").replace("\r\n", "\n").strip()
    msgs = parse_thread(text, *me())
    them = next((m for m in msgs if m["me"] is False and m["email"]), None) or next((m for m in msgs if m["me"] is False), None)
    if not msgs or them is None:
        raise ValueError("couldn't find a message from someone other than you - is this an email thread?")
    existing = next((c for c in all_conversations() if them["email"] and c.get("email") == them["email"]), None)
    if existing:
        before = {_key(m) for m in existing["messages"]}
        if all(_key(m) in before for m in msgs):
            return existing["id"], len(existing["messages"]), 0
        meta = {k: existing[k] for k in ("contact", "email", "agency", "phone", "status", "jobs", "notes") if k in existing}
        _write(existing["id"], meta, existing["pastes"] + [(dt.datetime.now().strftime("%Y-%m-%d %H:%M"), text)])
        new = load(existing["id"])
        return existing["id"], len(new["messages"]), len(new["messages"]) - len(existing["messages"])
    first = next((m["date"] for m in msgs if m["date"]), today())
    agency = agency.strip() or agency_from(them["email"])
    conv_id = f"{first}-{slugify(agency or 'direct', 20)}-{slugify(them['name'] or them['email'], 30)}"
    meta = {"contact": them["name"], "email": them["email"], "agency": agency, "phone": "", "status": "active",
            "jobs": [], "notes": ""}
    _write(conv_id, meta, [(dt.datetime.now().strftime("%Y-%m-%d %H:%M"), text)])
    return conv_id, len(msgs), len(msgs)


def update(conv_id, **fields):
    c = load(conv_id)
    meta = {k: c.get(k, "") for k in ("contact", "email", "agency", "phone", "status", "jobs", "notes")}
    meta.update({k: v for k, v in fields.items() if v is not None})
    _write(conv_id, meta, c["pastes"])
    return meta


def link_job(conv_id, job_id):
    """Mark a job as coming via this recruiter (both ways)."""
    from . import jobs
    c = load(conv_id)
    if job_id not in c["jobs"]:
        update(conv_id, jobs=c["jobs"] + [job_id])
    p = jobs.job_dir(job_id) / "posting.md"
    meta, body = split_front_matter(p.read_text(encoding="utf-8"))
    meta["via"] = conv_id
    p.write_text(join_front_matter(meta, body), encoding="utf-8")
