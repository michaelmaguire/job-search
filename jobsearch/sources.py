"""Curate search sources: search/companies.yaml entries (company boards) and the job-site switches
under `sources:` in config.yaml.

companies.yaml is hand-commented, so it is edited line by line (one flow-style
entry per line) instead of being re-dumped - comments and grouping survive.
An entry with a `board` is polled by `jobsearch fetch`; without one it is in
the `manual` list (careers page checked by hand).
"""
import re

import yaml

from .paths import SEARCH

SECTIONS = ("polled", "manual")
ENTRY_RE = re.compile(r"^\s+- \{.*\}\s*$")
KEYS = ("name", "board", "careers", "history")


def companies_file():
    cfg = yaml.safe_load((SEARCH / "config.yaml").read_text(encoding="utf-8"))
    return SEARCH / cfg["sources"].get("companies_file", "companies.yaml")


def _lines():
    return companies_file().read_text(encoding="utf-8").splitlines()


def _save(lines):
    companies_file().write_text("\n".join(lines) + "\n", encoding="utf-8")


def _bounds(lines, section):
    """(header index, end index) of a top-level section; end is exclusive."""
    start = next((i for i, line in enumerate(lines) if line.rstrip() == f"{section}:"), None)
    if start is None:
        return None, None
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^[A-Za-z_]\w*:", lines[i])), len(lines))
    return start, end


def entries():
    """{'polled': [...], 'manual': [...]}; each entry has its line number and the comment group above it."""
    lines, out = _lines(), {s: [] for s in SECTIONS}
    for section in SECTIONS:
        start, end = _bounds(lines, section)
        if start is None:
            continue
        group = ""
        for i in range(start + 1, end):
            line = lines[i]
            if line.strip().startswith("#") and line.startswith("  "):
                group = line.strip().lstrip("#").strip()
            elif ENTRY_RE.match(line):
                e = yaml.safe_load(line.strip()[2:]) or {}
                out[section].append({**{k: str(e.get(k, "") or "") for k in KEYS}, "line": i, "group": group})
    return out


def _dump(entry):
    d = {k: entry[k].strip() for k in KEYS if entry.get(k, "").strip()}
    return "  - " + yaml.safe_dump(d, default_flow_style=True, sort_keys=False, allow_unicode=True, width=10_000).strip()


def normalize_board(board):
    """Accept a pasted Workday careers URL: https://acme.wd3.myworkdayjobs.com/en-US/Careers -> workday:acme.wd3/Careers."""
    board = (board or "").strip()
    m = re.match(r"https?://([\w-]+\.wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([\w-]+)", board)
    if m:
        return f"workday:{m.group(1)}/{m.group(2)}"
    m = re.match(r"successfactors:\s*(?:https?://)?([\w.-]+)", board)  # successfactors:https://host/... -> host
    return f"successfactors:{m.group(1)}" if m else board


def validate(entry, ignore_line=None):
    from .fetch import FETCHERS, JOB_SITES
    errors = []
    if not entry.get("name", "").strip():
        errors.append("name is required")
    board = entry.get("board", "").strip()
    if board.startswith("workday:") and not re.fullmatch(r"workday:[\w-]+\.wd\d+/[\w-]+", board):
        errors.append("Workday boards look like workday:<company>.wd3/<site> - or paste the careers URL "
                      "(https://<company>.wd3.myworkdayjobs.com/<site>)")
    if board.startswith("successfactors:") and not re.fullmatch(r"successfactors:[\w.-]+\.[a-z]{2,}", board):
        errors.append("SuccessFactors boards are the career site's host, e.g. successfactors:careers.swissre.com")
    if board:
        ats, _, slug = board.partition(":")
        supported = sorted(k for k in FETCHERS if k not in JOB_SITES)
        if ats not in supported or not slug:
            errors.append(f"board must look like <ats>:<name>, with ats one of {', '.join(supported)}")
        dupes = [e for e in entries()["polled"] if e["board"] == board and e["line"] != ignore_line]
        if dupes:
            errors.append(f"{board} is already polled (as {dupes[0]['name']})")
    careers = entry.get("careers", "").strip()
    if careers and not careers.startswith(("http://", "https://")):
        errors.append("careers must be a URL")
    return errors


def _check_line(lines, line, name):
    if not (0 <= line < len(lines)) or not ENTRY_RE.match(lines[line]) \
            or str((yaml.safe_load(lines[line].strip()[2:]) or {}).get("name", "")) != name:
        raise ValueError("companies.yaml changed since the page was loaded - reload and try again")


def add(entry):
    """Append to 'polled' if it has a board, else to 'manual'. Returns errors."""
    entry = {**entry, "board": normalize_board(entry.get("board", ""))}
    errors = validate(entry)
    if errors:
        return errors
    lines = _lines()
    section = "polled" if entry.get("board", "").strip() else "manual"
    start, end = _bounds(lines, section)
    if start is None:
        lines += ["", f"{section}:"]
        start, end = len(lines) - 1, len(lines)
    last = max([i for i in range(start + 1, end) if ENTRY_RE.match(lines[i])], default=start)
    lines.insert(last + 1, _dump(entry))
    _save(lines)
    return []


def update(line, name, entry):
    """Rewrite an entry in place, or move it to the other section if its board was added/removed."""
    entry = {**entry, "board": normalize_board(entry.get("board", ""))}
    errors = validate(entry, ignore_line=line)
    if errors:
        return errors
    lines = _lines()
    _check_line(lines, line, name)
    old = yaml.safe_load(lines[line].strip()[2:]) or {}
    if bool(old.get("board")) == bool(entry.get("board", "").strip()):
        lines[line] = _dump(entry)
        _save(lines)
        return []
    del lines[line]
    _save(lines)
    return add(entry)


def remove(line, name):
    lines = _lines()
    _check_line(lines, line, name)
    del lines[line]
    _save(lines)


def site_enabled(site):
    cfg = yaml.safe_load((SEARCH / "config.yaml").read_text(encoding="utf-8"))
    return bool((cfg.get("sources") or {}).get(site))


def set_site(site, on):
    """Switch a job site on/off under `sources:` in config.yaml, keeping comments."""
    from .fetch import JOB_SITES
    if site not in JOB_SITES:
        raise ValueError(f"unknown job site {site}")
    p = SEARCH / "config.yaml"
    text = p.read_text(encoding="utf-8")
    value = "true" if on else "false"
    text, n = re.subn(rf"^(\s+{re.escape(site)}:\s*)(true|false)\b", lambda m: m.group(1) + value, text, count=1, flags=re.M)
    if not n:
        text, n = re.subn(r"^sources:\s*$", f"sources:\n  {site}: {value}", text, count=1, flags=re.M)
    if not n:
        raise ValueError("no 'sources:' section in config.yaml")
    p.write_text(text, encoding="utf-8")


def job_sites(counts=None):
    """The job sites with their on/off state, for the Sources page."""
    from .blocklist import Checker
    from .fetch import JOB_SITES
    checker, counts = Checker(), counts or {}
    return [{"key": k, **v, "on": site_enabled(k), "blocked": bool(checker.source(f"{k}:{k}")),
             "count": sum(n for src, n in counts.items() if src.split(":")[0] == k)} for k, v in JOB_SITES.items()]


def test_board(board):
    """Fetch one board now (nothing is saved). Returns (total adverts, adverts passing the filters).
    For Workday sites 'total' is the Swiss postings only."""
    from .fetch import FETCHERS, load_config, matches
    ats, _, slug = board.partition(":")
    cfg = load_config()
    found = list(FETCHERS[ats](slug))
    return len(found), sum(1 for j in found if matches(cfg, j))


def set_min_salary(value):
    """Set min_salary in config.yaml (CHF/year, 0 = off), keeping the file's comments."""
    if value < 0:
        raise ValueError("minimum salary can't be negative")
    p = SEARCH / "config.yaml"
    text = p.read_text(encoding="utf-8")
    new, n = re.subn(r"^min_salary:.*$", f"min_salary: {value}", text, count=1, flags=re.M)
    if not n:
        new = text.rstrip("\n") + f"\n\n# Minimum acceptable salary, CHF per year (0 = off).\nmin_salary: {value}\n"
    p.write_text(new, encoding="utf-8")
