"""Blocked sources and blocked companies (search/blocklist.yaml), tracked separately.

- A blocked source (a job site or one company's board, e.g. `swissdevjobs` or
  `greenhouse:acme`) is not fetched, and adverts already fetched from it are
  hidden from the inbox.
- A blocked company is hidden wherever its adverts come from, including job
  sites you still use, and new adverts from it are not saved by fetch.
"""
import re
import unicodedata

import yaml

from .jobs import today
from .paths import SEARCH

FILE = SEARCH / "blocklist.yaml"
HEADER = "# Edited by the web app (Sources and Companies tabs) - see jobsearch/blocklist.py.\n"
LEGAL = r"(ag|sa|sagl|gmbh|inc|ltd|llc|plc|limited|corp|corporation|co|bv|nv|se|srl|spa)"


def load():
    data = yaml.safe_load(FILE.read_text(encoding="utf-8")) if FILE.exists() else None
    data = data or {}
    return {"sources": data.get("sources") or [], "companies": data.get("companies") or []}


def save(data):
    FILE.write_text(HEADER + yaml.safe_dump(data, sort_keys=False, allow_unicode=True, width=10_000), encoding="utf-8")


def norm_company(name):
    """'Swissquote Bank SA' and 'swissquote bank' compare equal; '(Lugano)' etc. is ignored."""
    s = unicodedata.normalize("NFKD", str(name or "")).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"\(.*?\)", " ", s).replace("&", " and ")
    s = re.sub(r"[^a-z0-9]+", " ", s).strip()
    s = re.sub(rf"(\s+{LEGAL})+$", "", s)
    return s


def source_matches(blocked, source):
    """`swissdevjobs` blocks `swissdevjobs:swissdevjobs`; `greenhouse:acme` blocks only that board."""
    source = source or ""
    return source == blocked or source.startswith(blocked + ":")


class Checker:
    """Load the blocklist once and answer 'is this blocked, and why?'."""

    def __init__(self, data=None):
        data = data or load()
        self.sources = [b["source"] for b in data["sources"]]
        self.companies = {}
        for b in data["companies"]:
            for n in [b["name"], *(b.get("aliases") or [])]:
                self.companies[norm_company(n)] = b["name"]

    def source(self, source):
        return next((b for b in self.sources if source_matches(b, source)), None)

    def company(self, company):
        return self.companies.get(norm_company(company))

    def reason(self, job):
        """'source X' / 'company Y' if the job (posting.md metadata) is blocked, else None."""
        if s := self.source(job.get("source")):
            return f"source {s}"
        if c := self.company(job.get("company")):
            return f"company {c}"
        return None


def _validate_source(source):
    from .fetch import FETCHERS, JOB_SITES
    ats = source.partition(":")[0]
    if not re.fullmatch(r"[\w.-]+(:[\w.-]+)?", source):
        return "source must look like <site> or <ats>:<board>, e.g. swissdevjobs or greenhouse:acme"
    if ":" not in source and ats in FETCHERS and ats not in JOB_SITES:
        return f"'{source}' would block every {source} board - give one board, e.g. {source}:acme"
    return None


def block_source(source, reason=""):
    source = source.strip()
    data = load()
    if err := _validate_source(source):
        return [err]
    if any(b["source"] == source for b in data["sources"]):
        return [f"{source} is already blocked"]
    data["sources"].append({"source": source, "reason": reason.strip(), "blocked": today()})
    save(data)
    return []


def unblock_source(source):
    data = load()
    data["sources"] = [b for b in data["sources"] if b["source"] != source]
    save(data)


def block_company(name, aliases=(), reason=""):
    name = name.strip()
    if not norm_company(name):
        return ["company name is required"]
    data = load()
    if Checker(data).company(name):
        return [f"{name} is already blocked"]
    entry = {"name": name, "reason": reason.strip(), "blocked": today()}
    aliases = [a.strip() for a in aliases if a.strip() and norm_company(a) != norm_company(name)]
    if aliases:
        entry["aliases"] = aliases
    data["companies"].append(entry)
    save(data)
    return []


def unblock_company(name):
    data = load()
    data["companies"] = [b for b in data["companies"] if b["name"] != name]
    save(data)
