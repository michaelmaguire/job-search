"""Poll job boards, save new matching adverts as jobs/<id>/posting.md (status: new).

Sources: the job sites in JOB_SITES switched on in search/config.yaml, and every `polled`
entry in search/companies.yaml. search/seen.txt remembers fetched URLs so an
advert is only added once, even after you delete or skip it. Blocked sources are
not fetched and adverts from blocked companies are not saved (search/blocklist.yaml).
"""
import datetime as dt
import html
import logging
import re
import subprocess
import xml.etree.ElementTree as ET

import requests
import yaml

from . import alerts
from .blocklist import Checker
from .jobs import (
    JOBS,
    all_jobs,
    SWISS_RE,
    below_salary,
    create_job,
    extract_salary,
    html_to_text,
    join_front_matter,
    outside_commute,
    split_front_matter,
    today,
)
from .paths import DATA, SEARCH

log = logging.getLogger("jobsearch.fetch")
UA = {"User-Agent": "Mozilla/5.0 (personal job search script)"}


def get(url, **kw):
    r = requests.get(url, headers=UA, timeout=30, **kw)
    r.raise_for_status()
    return r


def day(value):
    """Board timestamp (ISO string, or epoch milliseconds) -> YYYY-MM-DD, or '' if missing."""
    if not value:
        return ""
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).date().isoformat()
    return str(value)[:10]


# Each fetcher yields dicts: company, title, location, url, posted, text (+ optional extra, ref)
def greenhouse(board):
    for j in get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs", params={"content": "true"}).json()["jobs"]:
        yield dict(company=j.get("company_name") or board, title=j["title"],
                   location=(j.get("location") or {}).get("name", ""), url=j["absolute_url"],
                   posted=day(j.get("first_published") or j.get("updated_at")),
                   text=html_to_text(html.unescape(j.get("content", ""))))


def lever(board):
    for j in get(f"https://api.lever.co/v0/postings/{board}", params={"mode": "json"}).json():
        parts = [j.get("descriptionPlain", "")]
        parts += [f"\n{lst.get('text', '')}\n" + html_to_text(lst.get("content", "")) for lst in j.get("lists", [])]
        parts.append(j.get("additionalPlain", ""))
        cats = j.get("categories", {})
        loc = ", ".join(cats.get("allLocations") or [cats.get("location") or ""])
        yield dict(company=board, title=j["text"], location=f"{loc} {j.get('workplaceType', '')}".strip(),
                   url=j["hostedUrl"], posted=day(j.get("createdAt")), text="\n".join(p for p in parts if p))


def ashby(board):
    for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{board}").json().get("jobs", []):
        locs = [j.get("location", "")] + [s.get("location", "") for s in j.get("secondaryLocations", [])]
        remote = " Remote" if j.get("isRemote") else ""
        yield dict(company=board.split(".")[0], title=j["title"], location="; ".join(place for place in locs if place) + remote,
                   url=j["jobUrl"], posted=day(j.get("publishedAt")), text=j.get("descriptionPlain") or html_to_text(j.get("descriptionHtml", "")))


def smartrecruiters(company):
    data = get(f"https://api.smartrecruiters.com/v1/companies/{company}/postings", params={"limit": 100}).json()
    for j in data.get("content", []):
        loc = j.get("location", {})
        yield dict(company=j.get("company", {}).get("name", company), title=j["name"],
                   location=(loc.get("fullLocation") or loc.get("city", "")) + (" Remote" if loc.get("remote") else ""),
                   url=f"https://jobs.smartrecruiters.com/{company}/{j['id']}", posted=day(j.get("releasedDate")),
                   text=None, ref=j["ref"])


def smartrecruiters_text(ref):
    secs = (get(ref).json().get("jobAd") or {}).get("sections", {})
    return "\n\n".join(f"## {s.get('title', '')}\n{html_to_text(s.get('text', ''))}"
                       for s in secs.values() if isinstance(s, dict) and s.get("text"))


def post(url, payload):
    r = requests.post(url, json=payload, headers={**UA, "Accept": "application/json"}, timeout=30)
    r.raise_for_status()
    return r.json()


def _workday_swiss_facets(facets):
    """appliedFacets selecting Switzerland, from a Workday site's facet list (country facet preferred)."""
    found = []

    def walk(items):
        for f in items:
            for v in f.get("values", []):
                if "facetParameter" in v:
                    walk([v])
                elif "switzerland" in str(v.get("descriptor", "")).lower() or "schweiz" in str(v.get("descriptor", "")).lower():
                    found.append((f.get("facetParameter"), v.get("id")))
    walk(facets)
    if not found:  # no "Switzerland" value (e.g. Roche): pick Swiss cities from location facets
        def cities(items):
            for f in items:
                for v in f.get("values", []):
                    if "facetParameter" in v:
                        cities([v])
                    elif "location" in str(f.get("facetParameter", "")).lower() and SWISS_RE.search(str(v.get("descriptor", ""))):
                        found.append((f.get("facetParameter"), v.get("id")))
        cities(facets)
    countries = [(p, i) for p, i in found if "ountr" in str(p)]
    chosen = countries or found
    applied = {}
    for param, value in chosen:
        applied.setdefault(param, []).append(value)
    return applied


def _workday_posted(text):
    """'Posted Today' / 'Posted Yesterday' / 'Posted 3 Days Ago' / 'Posted 30+ Days Ago' -> date."""
    text = (text or "").lower()
    if "today" in text:
        days = 0
    elif "yesterday" in text:
        days = 1
    elif m := re.search(r"(\d+)\+?\s*day", text):
        days = int(m.group(1))
    else:
        return ""
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


WORKDAY_MAX = 1000  # postings per site and run, as a safety cap


def workday(board):
    """board = '<tenant>.wd<N>/<site>' as in https://<tenant>.wd<N>.myworkdayjobs.com/<site>.
    Only Swiss postings are listed when the site offers a Switzerland facet."""
    host_part, _, site = board.partition("/")
    tenant = host_part.split(".")[0]
    host = f"https://{host_part}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    first = post(f"{api}/jobs", {"appliedFacets": {}, "limit": 20, "offset": 0, "searchText": ""})
    applied = _workday_swiss_facets(first.get("facets", []))
    total, offset = None, 0
    while offset < WORKDAY_MAX:
        data = post(f"{api}/jobs", {"appliedFacets": applied, "limit": 20, "offset": offset, "searchText": ""})
        total = data.get("total") if total is None else total  # later pages may report 0
        postings = data.get("jobPostings") or []
        for p in postings:
            loc = p.get("locationsText") or ""
            if applied and "switzerland" not in loc.lower():
                loc = f"{loc}, Switzerland" if loc else "Switzerland"  # filtered to Swiss jobs already
            yield dict(company=tenant.capitalize(), title=p.get("title", ""), location=loc,
                       url=f"{host}/{site}{p.get('externalPath', '')}", posted=_workday_posted(p.get("postedOn")),
                       text=None, ref=f"{api}{p.get('externalPath', '')}")
        offset += 20
        if not postings or offset >= (total or 0):
            break


def workday_detail(ref):
    """Full advert for one Workday posting: text, exact locations, start date."""
    info = get(ref).json().get("jobPostingInfo") or {}
    locs = [info.get("location") or ""] + list(info.get("additionalLocations") or [])
    out = {"text": html_to_text(info.get("jobDescription") or "")}
    if any(locs):
        out["location"] = "; ".join(place for place in locs if place)
    if info.get("startDate"):
        out["posted"] = day(info["startDate"])
    return out


SUCCESSFACTORS_MAX = 2000  # postings per site and run, as a safety cap


def successfactors(board):
    """board = the host of a SuccessFactors Career Site Builder site, e.g. 'careers.swissre.com'.
    Reads the site's search result pages (all locations - its own location search is unreliable);
    the location filter in config.yaml then picks the Swiss ones."""
    from bs4 import BeautifulSoup

    from .conversations import parse_when
    base = f"https://{board.strip('/')}"
    start, total = 0, None
    while start < SUCCESSFACTORS_MAX:
        soup = BeautifulSoup(get(f"{base}/search/", params={"q": "", "locationsearch": "", "startrow": start}).text, "html.parser")
        if total is None:
            label = soup.select_one(".paginationLabel")
            nums = re.findall(r"\d+", label.get_text(" ", strip=True)) if label else []
            total = int(nums[-1]) if nums else 0
        rows = soup.select("tr.data-row")
        for r in rows:
            a = r.select_one("a.jobTitle-link")
            if not a or not a.get("href"):
                continue
            loc = r.select_one(".jobLocation")
            date = r.select_one(".jobDate")
            loc = " ".join(loc.get_text(" ", strip=True).split()) if loc else ""
            if re.search(r"(^|,\s*)CH$", loc):
                loc += " (Switzerland)"
            yield dict(company=board.split(".")[-2].capitalize(), title=a.get_text(" ", strip=True), location=loc,
                       url=base + a["href"], posted=parse_when(date.get_text(" ", strip=True))[0] if date else "",
                       text=None, ref=base + a["href"])
        start += len(rows)
        if not rows or start >= total:
            break


def successfactors_detail(ref):
    """Full advert from a SuccessFactors job page: description and posting date."""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(get(ref).text, "html.parser")
    desc = soup.select_one("span.jobdescription") or soup.select_one("[itemprop=description]")
    out = {"text": html_to_text(str(desc)) if desc else ""}
    posted = soup.select_one("[itemprop=datePosted]")
    if posted and (m := re.search(r"([A-Z][a-z]{2}) (\d{1,2}) .* (\d{4})", posted.get("content") or "")):
        from .conversations import parse_when
        out["posted"] = parse_when(f"{m.group(1)} {m.group(2)}, {m.group(3)}")[0]
    return out


def recruitee(board):
    for j in get(f"https://{board}.recruitee.com/api/offers/").json().get("offers", []):
        yield dict(company=j.get("company_name") or board, title=j["title"],
                   location=(j.get("location") or "") + (" Remote" if j.get("remote") else ""),
                   url=j["careers_url"], posted=day(j.get("published_at") or j.get("created_at")), text=html_to_text((j.get("description") or "") + (j.get("requirements") or "")))


def personio(board):
    root = ET.fromstring(get(f"https://{board}.jobs.personio.de/xml").content)
    for pos in root.iter("position"):
        offices = [pos.findtext("office") or ""] + [o.text or "" for o in pos.iter("additionalOffice")]
        descs = [f"## {d.findtext('name')}\n{html_to_text(d.findtext('value'))}" for d in pos.iter("jobDescription")]
        yield dict(company=pos.findtext("subcompany") or board, title=pos.findtext("name"),
                   location="; ".join(o for o in offices if o),
                   url=f"https://{board}.jobs.personio.de/job/{pos.findtext('id')}",
                   posted=day(pos.findtext("createdAt")), text="\n\n".join(descs))


def swissdevjobs(_):
    for j in get("https://swissdevjobs.ch/api/jobsLight").json():
        if j.get("isPaused"):
            continue
        sal = f"CHF {j['annualSalaryFrom']:,}-{j.get('annualSalaryTo') or 0:,}" if j.get("annualSalaryFrom") else ""
        text = ("(swissdevjobs.ch only provides a summary - open the URL and paste the full advert below "
                "before writing a letter.)\n\n"
                f"- Workplace: {j.get('workplace', '')}\n- Level: {j.get('expLevel', '')}\n"
                f"- Technologies: {', '.join(j.get('technologies', []))}\n- Salary: {sal}\n"
                f"- Company size: {j.get('companySize', '')}  type: {j.get('companyType', '')}\n"
                f"- Ad language: {j.get('language', '')}\n")
        yield dict(company=j["company"], title=j["name"],
                   location=f"{j.get('actualCity', '')}, Switzerland ({j.get('workplace', '')})",
                   url=f"https://swissdevjobs.ch/jobs/{j['jobUrl']}", posted=day(j.get("activeFrom")), text=text,
                   extra={"salary": sal, "summary_only": True})


FETCHERS = {"greenhouse": greenhouse, "lever": lever, "ashby": ashby, "smartrecruiters": smartrecruiters,
            "recruitee": recruitee, "personio": personio, "workday": workday, "successfactors": successfactors,
            "swissdevjobs": swissdevjobs, **{k: alerts.fetcher(k) for k in alerts.ALERTS}}

# Boards whose listings come without the advert text: fetched per posting, only for adverts that pass
# the location, title and blocklist checks. Returns {"text": ..., optionally "location", "posted"}.
DETAILS = {"smartrecruiters": lambda ref: {"text": smartrecruiters_text(ref)}, "workday": workday_detail,
           "successfactors": successfactors_detail}

# Job sites: one source listing many companies' adverts, switched on/off under `sources:` in
# config.yaml (e.g. `swissdevjobs: true`). Company boards are listed in companies.yaml instead.
# To add a site: write its fetcher above, register it in FETCHERS and describe it here.
JOB_SITES = {
    "swissdevjobs": {"name": "swissdevjobs.ch", "url": "https://swissdevjobs.ch",
                     "about": "Developer adverts from hundreds of Swiss companies. Summaries only - paste the full "
                              "advert before writing a letter. The only source that states salary ranges."},
    # job-alert emails read from Gmail (alerts.py), e.g. LinkedIn's
    **{k: {f: a[f] for f in ("name", "url", "about")} for k, a in alerts.ALERTS.items()},
}


def load_config():
    return yaml.safe_load((SEARCH / "config.yaml").read_text())


def boards(cfg, blocked=None):
    """(source, board) pairs from config.yaml plus companies.yaml 'polled' entries,
    minus blocked sources and the boards of blocked companies."""
    blocked = blocked or Checker()
    src = cfg["sources"]
    for site in JOB_SITES:
        if src.get(site) and not blocked.source(f"{site}:{site}"):
            yield site, site
    if src.get("companies_file"):
        comp = yaml.safe_load((SEARCH / src["companies_file"]).read_text())
        for c in comp.get("polled") or []:
            if blocked.source(c["board"]) or blocked.company(c.get("name")):
                continue
            ats, _, name = c["board"].partition(":")
            yield ats, name


def company_names(cfg):
    """board -> company name, from companies.yaml."""
    src = cfg.get("sources") or {}
    if not src.get("companies_file"):
        return {}
    comp = yaml.safe_load((SEARCH / src["companies_file"]).read_text()) or {}
    return {c["board"]: c.get("name", "") for c in comp.get("polled") or []}


def matches(cfg, job):
    f, title = cfg["filters"], job["title"]
    return (any(re.search(p, title, re.I) for p in f["title_include"])
            and not any(re.search(p, title, re.I) for p in f["title_exclude"])
            and any(re.search(p, job["location"] or "", re.I) for p in f["locations"]))


def score(cfg, job):
    blob = f"{job['title']}\n{job.get('text') or ''}"
    return sum(w for pat, w in cfg["score_terms"].items() if re.search(pat, blob, re.I))


def undated_jobs():
    """{url: posting.md path} for saved adverts without a posted date (filled in on the next fetch)."""
    out = {}
    for p in JOBS.glob("*/posting.md") if JOBS.exists() else []:
        meta, _ = split_front_matter(p.read_text(encoding="utf-8"))
        if meta.get("url") and not meta.get("posted"):
            out[meta["url"]] = p
    return out


def set_posted(path, posted):
    meta, body = split_front_matter(path.read_text(encoding="utf-8"))
    meta["posted"] = posted
    path.write_text(join_front_matter(meta, body), encoding="utf-8")


def same_url(url):
    """A link without tracking parameters, for spotting an advert you already saved some other way
    (e.g. a LinkedIn job added by hand from a tracking link, later in an alert email)."""
    u = str(url or "").split("#")[0].split("?")[0].rstrip("/").lower()
    return re.sub(r"^https?://(www\.)?", "", u).replace("linkedin.com/comm/jobs/", "linkedin.com/jobs/")


def board_label(source, board, names):
    """What a board is called in progress messages: 'Elastic (greenhouse:elastic)', 'swissdevjobs.ch'."""
    if source in JOB_SITES:
        return JOB_SITES[source]["name"]
    name = names.get(f"{source}:{board}")
    return f"{name} ({source}:{board})" if name else f"{source}:{board}"


def run(dry_run=False, commit=False, progress=None):
    """Poll every source. `progress(event)` (optional) hears about each board as it goes: a dict with
    step, total, label and status 'checking' | 'advert' (title) | 'done' (listed, new) | 'error' (error).
    The same goes to the 'jobsearch.fetch' log."""
    cfg = load_config()
    seen_file = SEARCH / "seen.txt"
    seen = set(seen_file.read_text().split()) if seen_file.exists() else set()
    new, errors = [], []
    blocked = Checker()
    undated = {} if dry_run else undated_jobs()
    saved = {same_url(j.get("url")) for j in all_jobs() if j.get("url")}

    names = company_names(cfg)
    todo = list(boards(cfg, blocked))

    def tell(**event):
        if event["status"] == "checking":
            log.info("[%d/%d] checking %s", event["step"], event["total"], event["label"])
        elif event["status"] == "advert":
            log.info("    reading advert: %s", event["title"])
        elif event["status"] == "done":
            log.info("    %s: %d adverts listed, %d new", event["label"], event["listed"], event["new"])
        else:
            log.warning("    %s failed: %s", event["label"], event["error"])
        if progress:
            try:
                progress(event)
            except Exception:  # a broken progress display must not stop the fetch
                pass

    log.info("fetch started: %d sources", len(todo))
    for step, (source, board) in enumerate(todo, 1):
        label, listed, before = board_label(source, board, names), 0, len(new)
        where = {"step": step, "total": len(todo), "label": label}
        tell(status="checking", **where)
        try:
            for job in FETCHERS[source](board):
                listed += 1
                if source in ("workday", "successfactors"):  # these only know a host; use the companies.yaml name
                    job["company"] = names.get(f"{source}:{board}", job["company"])
                if job["url"] in undated and job.get("posted"):
                    set_posted(undated.pop(job["url"]), job["posted"])
                if job["url"] in seen or same_url(job["url"]) in saved or not matches(cfg, job) \
                        or blocked.company(job["company"]):
                    continue
                if job.get("text") is None and job.get("ref") and source in DETAILS:
                    tell(status="advert", title=job["title"], **where)
                    job.update({k: v for k, v in DETAILS[source](job["ref"]).items() if v})
                if not (job.get("extra") or {}).get("salary") and job.get("text"):
                    if found := extract_salary(job["text"]):
                        job["extra"] = {**(job.get("extra") or {}), "salary": found}
                if below_salary({**(job.get("extra") or {}), "location": job.get("location")}, cfg.get("min_salary") or 0) \
                        or outside_commute(job, cfg.get("commute_area") or []):
                    continue
                s = score(cfg, job) + alerts.score_bonus(cfg, source)
                if s < cfg.get("min_score", 0):
                    continue
                seen.add(job["url"])
                job_id = "(dry run)" if dry_run else create_job(
                    job["company"], job["title"], job["location"], job["url"], f"{source}:{board}",
                    job["text"] or "", s, {"posted": job.get("posted"), **(job.get("extra") or {})})
                new.append({**job, "score": s, "id": job_id})
            tell(status="done", listed=listed, new=len(new) - before, **where)
        except Exception as e:  # one broken board shouldn't stop the run
            errors.append(f"{source}:{board}: {e}")
            tell(status="error", error=str(e), **where)

    new.sort(key=lambda r: -r["score"])
    lines = [f"# New matches {today()}", "", f"{len(new)} new matching adverts.", "",
             "| score | company | title | location | id |", "|---|---|---|---|---|"]
    lines += [f"| {r['score']} | {r['company']} | [{r['title']}]({r['url']}) | {r['location']} | {r['id']} |" for r in new]
    if errors:
        lines += ["", "## Source errors", ""] + [f"- {e}" for e in errors]
    digest = "\n".join(lines) + "\n"

    log.info("fetch finished: %d new adverts, %d source errors", len(new), len(errors))
    if not dry_run:
        seen_file.write_text("\n".join(sorted(seen)) + "\n")
        (SEARCH / "latest-digest.md").write_text(digest, encoding="utf-8")
        if commit and new:
            subprocess.run(["git", "-C", str(DATA), "add", "jobs", "search"], check=True)
            subprocess.run(["git", "-C", str(DATA), "commit", "-q", "-m", f"fetch: {len(new)} new adverts"], check=True)
    return digest, new, errors
