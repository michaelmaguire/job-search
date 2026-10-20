"""The browser version's server side: the local web app (jobsearch.web), run in Pyodide inside a Web Worker.

The data repo is checked out at /data and /settings holds per-browser settings, both in an in-memory file
system that the worker keeps encrypted in the browser. Pages are rendered by the same Flask routes and
templates as `jobsearch serve`, so they look and behave the same and write the same files. What a browser
tab can't do is swapped out here:
  - git: status comes from the worker (isomorphic-git), which also handles commit, import and export;
  - fetching runs during the request (no threads) through the browser's network - boards that allow it;
  - letters are rendered with letterdoc.py instead of LibreOffice;
  - Gmail (drafts, checking for replies), Claude drafting and Chrome snapshots need the local app (the pages say so).
"""
import datetime as dt
import io
import json
import os
from pathlib import Path

os.environ.setdefault("JOBSEARCH_DATA", "/data")  # tests point these elsewhere
os.environ.setdefault("JOBSEARCH_CONFIG", "/settings/config")  # insured.yaml (name, AHV) for the URC/RAV form
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome in a browser tab

import letterdoc  # noqa: E402
import yaml  # noqa: E402
from flask import abort, flash, redirect, render_template, request, url_for  # noqa: E402
from jinja2 import ChoiceLoader, FileSystemLoader  # noqa: E402
from werkzeug.datastructures import MultiDict  # noqa: E402

from jobsearch import datarepo, gmail, render, report, web  # noqa: E402  (must follow JOBSEARCH_DATA)
from jobsearch.paths import DATA  # noqa: E402

SETTINGS = Path(os.environ["JOBSEARCH_CONFIG"]).parent
AUTHOR = SETTINGS / "author.json"

REPO = {"ok": True, "changes": [], "branch": None, "ahead": 0}  # set by the worker (set_repo) before pages render
INFO = {}  # extra facts for the Data page (commits, vault), set by the worker
PENDING = []  # messages from actions the worker handled itself, shown on the next page
LAST_BODY = b""  # the last response's body, read by the worker as bytes

def _no_push(message=""):
    return False, "commits are made by the browser app"  # the worker answers /data/commit itself


def _fetch_now(self):
    """Fetcher.start without a thread (none in the browser): the fetch runs during the request."""
    if not self.lock.acquire(blocking=False):
        return False
    self.started = dt.datetime.now()
    self._run()
    return True


SWAPS = [
    (datarepo, "status", lambda: {**REPO, "changes": list(REPO["changes"])}),
    (datarepo, "commit_and_push", _no_push),
    (gmail, "status", lambda max_age=60: (False, "Gmail (drafts, checking for replies) needs the local app - for a draft, use Write in Gmail and attach "
                                          "the files yourself; paste replies on the Efforts page")),
    (render, "CONVERT", letterdoc.write),
    (web.Fetcher, "start", _fetch_now),
]
app = client = None


def start():
    """Make the browser swaps and create the app. Returns a function that undoes the swaps (for tests)."""
    global app, client
    originals = [(obj, name, getattr(obj, name)) for obj, name, _ in SWAPS]
    for obj, name, value in SWAPS:
        setattr(obj, name, value)
    app = web.create_app()
    app.config["BROWSER"] = True
    app.config["PROPAGATE_EXCEPTIONS"] = True  # the worker shows the error instead of a bare "Internal Server Error"
    app.jinja_env.loader = ChoiceLoader([app.jinja_env.loader, FileSystemLoader(Path(__file__).parent / "templates")])
    app.before_request(_pending)
    for rule, view, methods in ROUTES:
        app.add_url_rule(rule, view.__name__, view, methods=methods)
    client = app.test_client()
    return lambda: [setattr(obj, name, value) for obj, name, value in originals]


def _pending():
    while PENDING:
        flash(PENDING.pop(0))


def author():
    try:
        return {"name": "", "email": "", **json.loads(AUTHOR.read_text(encoding="utf-8"))}
    except (OSError, ValueError):
        return {"name": "", "email": ""}


EDITABLE = (".yaml", ".yml", ".md", ".csv", ".txt")


def editable_files():
    """Text files you'd otherwise edit in a text editor: the top level, profile/ and search/."""
    found = [p for p in DATA.glob("*") if p.is_file()] + [p for d in ("profile", "search") for p in (DATA / d).glob("*")]
    return sorted(str(p.relative_to(DATA)) for p in found if p.is_file() and p.suffix in EDITABLE)


def browser_data():
    return render_template("browser_data.html", info={**INFO, "files": editable_files()}, author=author(), me=report.insured())


def browser_edit():
    path = request.args.get("path", "")
    if path not in editable_files():
        abort(404)
    f = DATA / path
    if request.method == "POST":
        text = request.form.get("text", "").replace("\r\n", "\n")
        try:
            if f.suffix in (".yaml", ".yml"):
                yaml.safe_load(text)
        except yaml.YAMLError as e:
            flash(f"error: not saved - {path} isn't valid YAML: {e}")
            return render_template("browser_edit.html", path=path, text=text)
        f.write_text(text, encoding="utf-8")
        flash(f"saved {path}")
        return redirect(url_for("browser_edit", path=path))
    return render_template("browser_edit.html", path=path, text=f.read_text(encoding="utf-8"))


def browser_author():
    name, email = request.form.get("name", "").strip(), request.form.get("email", "").strip()
    if not name or "@" not in email:
        flash("error: a name and an email address are needed for commits")
    else:
        AUTHOR.parent.mkdir(parents=True, exist_ok=True)
        AUTHOR.write_text(json.dumps({"name": name, "email": email}), encoding="utf-8")
        flash(f"commits will be made as {name} <{email}>")
    return redirect(url_for("browser_data"))


def browser_insured():
    me = {**report.insured(), "name": request.form.get("name", "").strip(), "ahv": request.form.get("ahv", "").strip()}
    report.INSURED.parent.mkdir(parents=True, exist_ok=True)
    report.INSURED.write_text(yaml.safe_dump(me, allow_unicode=True, sort_keys=False), encoding="utf-8")
    flash("saved - used on the URC/RAV form")
    return redirect(url_for("browser_data"))


ROUTES = [("/browser/data", browser_data, ["GET"]), ("/browser/edit", browser_edit, ["GET", "POST"]),
          ("/browser/author", browser_author, ["POST"]), ("/browser/insured", browser_insured, ["POST"])]


# ---------------------------------------------------------------- called by the worker

def set_repo(state):
    REPO.update(json.loads(state))


def set_info(info):
    INFO.clear()
    INFO.update(json.loads(info))


def flash_later(message):
    PENDING.append(message)


def csrf():
    with client.session_transaction() as s:
        return json.dumps(s.get("csrf", ""))


def handle(method, path, fields="[]", files=()):
    """One request, redirects followed. files: uploaded files as (field, filename, bytes) - from the worker,
    bytes are a JS Uint8Array. Returns JSON: status, final path (+ #fragment), content type and download
    name; the body is in LAST_BODY (and in "text" for HTML)."""
    global LAST_BODY
    path, _, query = path.partition("?")
    data = None
    if method == "POST":
        data = MultiDict(json.loads(fields))
        for field, filename, content in files or ():
            content = content.to_bytes() if hasattr(content, "to_bytes") else bytes(content)
            data.add(str(field), (io.BytesIO(content), str(filename)))
    r = client.open(path, method=method, query_string=query, data=data, follow_redirects=True)
    final = r.request.path + (f"?{r.request.query_string.decode()}" if r.request.query_string else "")
    fragment = r.history[-1].headers.get("Location", "").partition("#")[2] if r.history else ""
    LAST_BODY = r.get_data()
    kind = r.headers.get("Content-Type", "")
    out = {"status": r.status_code, "path": final, "fragment": fragment, "type": kind,
           "download": r.headers.get("Content-Disposition", "").startswith("attachment"),
           "filename": (r.headers.get("Content-Disposition", "").partition("filename=")[2].strip('"')
                        or Path(path).name)}
    if kind.startswith("text/html"):
        out["text"] = LAST_BODY.decode("utf-8", "replace")
    r.close()
    return json.dumps(out)
