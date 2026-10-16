"""Job folders: jobs/<id>/posting.md holds the saved advert, with its status in YAML front matter."""
import copy
import datetime as dt
import re
from pathlib import Path

import yaml

from .paths import JOBS

STATUSES = ["new", "shortlisted", "drafting", "applied", "acknowledged", "interviewing", "offer",
            "rejected", "withdrawn", "skipped", "closed"]


def today():
    return dt.date.today().isoformat()


def slugify(text, maxlen=40):
    text = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return text[:maxlen].rstrip("-") or "x"


SafeLoader = getattr(yaml, "CSafeLoader", yaml.SafeLoader)  # libyaml when available: same result, much faster


def split_front_matter(text):
    m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.S)
    if not m:
        return {}, text
    return yaml.load(m.group(1), Loader=SafeLoader) or {}, m.group(2)


def join_front_matter(meta, body):
    return "---\n" + yaml.safe_dump(meta, sort_keys=False, allow_unicode=True, width=1000) + "---\n" + body


def html_to_text(html):
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html or "", "html.parser")
    for li in soup.find_all("li"):
        li.insert_before("\n- ")
    for tag in soup.find_all(["p", "div", "br", "h1", "h2", "h3", "h4", "ul", "ol"]):
        tag.insert_after("\n")
    text = re.sub(r"[ \t\xa0]+", " ", soup.get_text())
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return "\n".join(line.strip() for line in text.splitlines()).strip()


def job_dir(job):
    """Accept a job id or a path to a job folder."""
    p = Path(job)
    if p.is_dir():
        return p.resolve()
    if (JOBS / job).is_dir():
        return JOBS / job
    raise SystemExit(f"no such job: {job}")


def create_job(company, title, location, url, source, text, score="", extra=None):
    job_id = f"{today()}-{slugify(company, 20)}-{slugify(title, 40)}"
    folder, n = JOBS / job_id, 2
    while folder.exists():
        folder, n = JOBS / f"{job_id}-{n}", n + 1
    folder.mkdir(parents=True)
    meta = {"company": company, "title": title, "location": location, "url": url, "source": source,
            "fetched": today(), "score": score, "status": "new", **(extra or {})}
    meta = {k: v for k, v in meta.items() if v not in (None, "")}
    (folder / "posting.md").write_text(join_front_matter(meta, f"\n# {title} - {company}\n\n{text.strip()}\n"),
                                       encoding="utf-8")
    return folder.name


def load(job):
    d = job_dir(job)
    meta, body = split_front_matter((d / "posting.md").read_text(encoding="utf-8"))
    return {"id": d.name, "dir": d, **meta, "body": body}


_postings = {}  # posting.md path -> (mtime, size, front matter): pages list every job, often several times


def all_jobs():
    if not JOBS.exists():
        return []
    out = []
    for d in sorted(JOBS.iterdir(), reverse=True):
        p = d / "posting.md"
        try:
            st = p.stat()
        except (FileNotFoundError, NotADirectoryError):  # a folder without an advert, or a file like .gitkeep
            continue
        cached = _postings.get(str(p))
        if not cached or cached[:2] != (st.st_mtime_ns, st.st_size):
            cached = (st.st_mtime_ns, st.st_size, split_front_matter(p.read_text(encoding="utf-8"))[0])
            _postings[str(p)] = cached
        out.append({"id": d.name, **copy.deepcopy(cached[2])})
    return out


def set_status(job, status):
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    d = job_dir(job)
    p = d / "posting.md"
    meta, body = split_front_matter(p.read_text(encoding="utf-8"))
    meta["status"] = status
    meta["status_changed"] = today()
    p.write_text(join_front_matter(meta, body), encoding="utf-8")
    from .snapshot import auto
    auto(d.name, status)  # PDF of the advert page, the first time it matters


CURRENCY = {"€": "EUR", "$": "USD", "£": "GBP", "FR": "CHF", "FR.": "CHF"}
_CUR = r"CHF|EUR|USD|GBP|€|\$|£|Fr\.?"
_AMT = r"\d{1,3}(?:[,.'’ ]\d{3})+|\d{2,3}\s?[kK]\b"
SALARY_RE = re.compile(rf"(?:({_CUR})\s?)?({_AMT})\s?({_CUR})?\s*(?:-|–|—|to|bis|à|a)\s*(?:({_CUR})\s?)?({_AMT})\s?({_CUR})?(?=(.{{0,40}}))",
                       re.I | re.S)


def _amount(text):
    text = text.strip()
    if text[-1] in "kK":
        return int(text[:-1].strip()) * 1000
    return int(re.sub(r"\D", "", text))


def extract_salary(text):
    """Highest salary range stated in an advert's text, normalised: 'EUR 50,000-77,000'. Needs a
    currency; per-month figures are annualised; hourly ones ignored. None if nothing plausible."""
    best, best_chf = None, -1
    for m in SALARY_RE.finditer(text or ""):
        cur = next((c for c in (m.group(1), m.group(3), m.group(4), m.group(6)) if c), None)
        if not cur:
            continue
        cur = CURRENCY.get(cur.upper(), cur.upper())
        lo, hi = _amount(m.group(2)), _amount(m.group(5))
        after = m.group(7).lower()
        if re.search(r"hour|stunde|/h\b|ora\b", after):
            continue
        if re.search(r"month|monat|mese|mois|/mo\b", after):
            lo, hi = lo * 12, hi * 12
        if not (20_000 <= hi <= 3_000_000 and lo <= hi):
            continue
        chf = hi * fx_rates().get(cur, 1)
        if chf > best_chf:
            best, best_chf = f"{cur} {lo:,}-{hi:,}", chf
    return best


_config = {}  # config.yaml read once per change: salary checks ask for it once per advert


def search_config():
    """search/config.yaml as a dict ({} if missing). Callers must not modify it."""
    from .paths import SEARCH
    f = SEARCH / "config.yaml"
    try:
        st = f.stat()
    except FileNotFoundError:
        return {}
    key = (str(f), st.st_mtime_ns, st.st_size)
    if _config.get("key") != key:
        _config.update(key=key, value=yaml.safe_load(f.read_text(encoding="utf-8")) or {})
    return _config["value"]


def fx_rates():
    """Currency -> CHF, from fx_to_chf in config.yaml (approximate; edit there)."""
    cfg = search_config()
    return {"CHF": 1.0, **{k.upper(): float(v) for k, v in ((cfg or {}).get("fx_to_chf") or {}).items()}}


def salary_max(meta, rates=None):
    """Top of an advert's stated salary range, converted to CHF/year: 'EUR 50,000-77,000' -> ~71,610.
    None if no salary, no upper bound, or an unknown currency."""
    text = str(meta.get("salary") or "")
    cur = (re.match(r"\s*([A-Za-z€$£]+)", text) or [None, "CHF"])[1].upper()
    cur = CURRENCY.get(cur, cur)
    nums = [int(n.replace(",", "").replace("'", "")) for n in re.findall(r"\d[\d,']*", text)]
    nums = [n for n in nums if n]
    rate = (rates or fx_rates()).get(cur)
    if len(nums) < 2 or rate is None:
        return None
    return round(max(nums) * rate)


def min_salary():
    cfg = search_config()
    return int((cfg or {}).get("min_salary") or 0)


SWISS_RE = re.compile(r"switzerland|schweiz|suisse|svizzera|\bch\b|z[uü]rich|gen[eè]v[ae]|genf|lausanne|basel|bern|\bzug\b|"
                      r"lugano|luzern|lucerne|winterthur|baden|aarau|neuch[aâ]tel|fribourg|st\.? ?gallen|"
                      r"rotkreuz|risch|baar|\bcham\b|schwyz|goldau|kaiseraugst|schlieren|d[uü]bendorf|uster|kloten|"
                      r"wallisellen|opfikon|glattbrugg|thalwil|horgen|w[aä]denswil|pf[aä]ffikon|freienbach|"
                      r"bellinzona|locarno|mendrisio|chiasso|manno|stabio|muttenz|allschwil|visp|sion|biel|bienne|"
                      r"vevey|nyon|gland|rolle|morges|solothurn|olten|chur|thun", re.I)


def below_salary(meta, limit=None):
    """'salary up to CHF 120,000' if the advert's stated range tops out below the limit, else None."""
    limit = min_salary() if limit is None else limit
    stated = str(meta.get("salary") or "")
    if stated and not stated.startswith("CHF") and SWISS_RE.search(str(meta.get("location") or "")):
        return None  # e.g. a Paris EUR range in a Geneva/Paris advert says nothing about Swiss pay
    top = salary_max(meta)
    if limit and top is not None and top < limit:
        stated = str(meta.get("salary"))
        return f"salary up to CHF {top:,} (< {limit:,})" + ("" if stated.startswith("CHF") else f", stated {stated}")
    return None


COUNTRY_WORDS = (r"switzerland|schweiz|suisse|svizzera|\bch\b|austria|[oö]sterreich|germany|deutschland|france|italy|italia|"
                 r"europe|emea|dach|remote|hybrid|on-?site|office|\(|\)")


def commute_patterns():
    cfg = search_config()
    return list((cfg or {}).get("commute_area") or [])


def outside_commute(meta, patterns=None):
    """'office outside commute area (Geneva)' if every location of a non-remote advert lies outside
    commute_area (config.yaml), else None. Empty commute_area, remote roles and a bare
    'Switzerland' are never flagged."""
    patterns = commute_patterns() if patterns is None else patterns
    loc = str(meta.get("location") or "").strip()
    if not patterns or not loc or re.search(r"remote", loc, re.I):
        return None
    if any(re.search(p, loc, re.I) for p in patterns):
        return None
    if not re.sub(r"[\s,;|/-]+", "", re.sub(COUNTRY_WORDS, "", loc, flags=re.I)):
        return None  # only "Switzerland"/"CH": no city to judge
    return f"office outside commute area ({loc.strip(' ;,')})"


def clean_url(url):
    """Drop tracking parameters; LinkedIn links become https://www.linkedin.com/jobs/view/<id>/."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
    url = (url or "").strip()
    if not url:
        return ""
    if "linkedin." in url:
        m = re.search(r"/jobs/view/(?:[^/?#]*?-)?(\d{6,})", url) or re.search(r"currentJobId=(\d{6,})", url)
        if m:
            return f"https://www.linkedin.com/jobs/view/{m.group(1)}/"
    parts = urlsplit(url)
    keep = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
            if not re.match(r"utm_|trk|refid|trackingid|src$|source$|gh_src|lever-source", k, re.I)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(keep), ""))


def source_for(url):
    """'linkedin', 'jobs.ch', or the site's domain - for adverts added by hand."""
    from urllib.parse import urlsplit
    host = urlsplit(url or "").netloc.lower().removeprefix("www.")
    if not host:
        return "manual"
    return "linkedin" if "linkedin." in host else host


def add_advert(company, title, location="", url="", source="", text="", posted="", status="shortlisted"):
    """Save an advert found elsewhere. If the same advert is already saved (same URL, or same company and
    title) and only as a summary, its text is replaced instead. Returns (job_id, what_happened, warnings)."""
    from .blocklist import norm_company
    url = clean_url(url)
    source = (source or "").strip() or source_for(url)
    text = (text or "").strip()
    for j in all_jobs():
        same = (url and j.get("url") == url) or (
            norm_company(j.get("company")) == norm_company(company) and str(j.get("title", "")).strip().lower() == title.strip().lower())
        if not same:
            continue
        if j.get("summary_only") and text:
            p = job_dir(j["id"]) / "posting.md"
            meta, _ = split_front_matter(p.read_text(encoding="utf-8"))
            meta.pop("summary_only", None)
            if not meta.get("salary") and (found := extract_salary(text)):
                meta["salary"] = found
            p.write_text(join_front_matter(meta, f"\n# {title} - {company}\n\n{text}\n"), encoding="utf-8")
            return j["id"], "replaced the summary of an advert already saved", []
        return j["id"], "already saved - nothing changed", []
    extra = {"posted": posted.strip() or None}
    if found := extract_salary(text):
        extra["salary"] = found
    try:
        from .fetch import load_config, score
        points = score(load_config(), {"title": title, "text": text})
    except Exception:
        points = ""
    job_id = create_job(company.strip(), title.strip(), location.strip(), url, source, text, points, extra)
    if status and status != "new":
        set_status(job_id, status)
    meta = {"location": location, "salary": extra.get("salary")}
    warnings = [w for w in (outside_commute(meta), below_salary(meta)) if w]
    return job_id, "added", warnings
