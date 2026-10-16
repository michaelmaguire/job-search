"""Save a PDF of an advert's web page as proof (for the RAV/URC) before the advert disappears.

Headless Chrome prints the page; its header and footer carry the capture date/time and the
URL. Saved as jobs/<id>/advert-YYYY-MM-DD.pdf. Taken automatically the first time a job is
shortlisted, drafting or applied (set JOBSEARCH_SNAPSHOT=off to disable), or on demand.

Chrome identifies itself as a normal (not "HeadlessChrome") browser, since some job boards refuse
headless Chrome outright, and a printed Chrome error page counts as a failure, not a snapshot.
"""
import functools
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading

from .jobs import job_dir, load, today

AUTO_STATUSES = {"shortlisted", "drafting", "applied"}
_lock = threading.Lock()


def chrome():
    for name in [os.environ.get("JOBSEARCH_CHROME"), "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]:
        if name and (path := shutil.which(name)):
            return path
    raise RuntimeError("Google Chrome/Chromium not found (set JOBSEARCH_CHROME to its path)")


@functools.lru_cache(maxsize=4)
def user_agent(path):
    """A regular desktop Chrome user agent for this Chrome's major version, or None if unknown."""
    try:
        version = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"(\d+)\.\d+\.\d+", version)
    if not m:
        return None
    platform = {"darwin": "Macintosh; Intel Mac OS X 10_15_7", "win32": "Windows NT 10.0; Win64; x64"}.get(
        sys.platform, "X11; Linux x86_64")
    return f"Mozilla/5.0 ({platform}) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{m[1]}.0.0.0 Safari/537.36"


# Chrome's own "can't load this page" screen, in the languages its UI may use; the ERR_ code is always shown.
ERROR_PAGE = re.compile(r"\b(ERR_[A-Z0-9_]{4,})\b|This site can.t be reached|Diese Website ist nicht erreichbar|"
                        r"Impossibile raggiungere il sito|Ce site est inaccessible")


def error_page(pdf):
    """The error shown, if the PDF is a Chrome error page rather than the advert; else None."""
    try:
        from pypdf import PdfReader
        text = PdfReader(str(pdf)).pages[0].extract_text() or ""
    except Exception:  # unreadable or empty: let the size check decide
        return None
    text = text[:3000]
    if not ERROR_PAGE.search(text):
        return None
    code = re.search(r"\bERR_[A-Z0-9_]{4,}\b", text)  # the most useful part of the message
    return code[0] if code else ERROR_PAGE.search(text)[0]


def existing(job):
    return sorted(job_dir(job).glob("advert-*.pdf"))


def take(job):
    """Print the advert URL to jobs/<id>/advert-<today>[-n].pdf and return the path."""
    d = job_dir(job)
    url = load(d).get("url")
    if not url:
        raise RuntimeError("this job has no URL")
    out, n = d / f"advert-{today()}.pdf", 2
    while out.exists():
        out, n = d / f"advert-{today()}-{n}.pdf", n + 1
    browser = chrome()
    agent = user_agent(browser)
    with tempfile.TemporaryDirectory() as profile:  # never touch your own Chrome profile
        subprocess.run([browser, "--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                        f"--user-data-dir={profile}", *([f"--user-agent={agent}"] if agent else []),
                        "--virtual-time-budget=10000", f"--print-to-pdf={out}", url],
                       capture_output=True, timeout=120)
    if not out.exists() or out.stat().st_size < 1000:
        out.unlink(missing_ok=True)
        raise RuntimeError(f"Chrome produced no PDF for {url}")
    if err := error_page(out):
        out.unlink()
        raise RuntimeError(f"the page didn't load in headless Chrome ({err}) - the site may block automated browsers; "
                           f"open it yourself and save it as PDF (Ctrl+P) into the job folder as advert-{today()}.pdf")
    (d / "advert-snapshot.error.txt").unlink(missing_ok=True)
    return out


def auto(job, status):
    """After a status change: snapshot once, in the background, if the job has none yet."""
    if status not in AUTO_STATUSES or os.environ.get("JOBSEARCH_SNAPSHOT") == "off" or existing(job):
        return

    def run():
        with _lock:
            if existing(job):
                return
            try:
                take(job)
            except Exception as e:
                (job_dir(job) / "advert-snapshot.error.txt").write_text(f"{today()}: {e}\n", encoding="utf-8")
    threading.Thread(target=run).start()  # not a daemon: a CLI run waits for it before exiting
