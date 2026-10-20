"""Local web app: inbox, job pages (advert, notes, letter + check), efforts log, monthly report, facts.

Runs on 127.0.0.1 only. Reads and writes the same files as the CLI, so it works
with or without Claude.
"""
import datetime as dt
import html
import re
import secrets
import threading

import yaml
from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)
from markupsafe import Markup

from . import (
    blocklist,
    conversations,
    correspondence,
    datarepo,
    efforts,
    gmail,
    jobs,
    sources,
)
from .check import check, report_text
from .drafter import Drafter
from .paths import DATA, FACTS, SEARCH


class Fetcher:
    """Runs fetch.run() in a background thread; the lock keeps it to one run at a time. Never commits."""

    def __init__(self):
        self.lock = threading.Lock()
        self.started = self.finished = None
        self.result = ""  # summary of the last run

    @property
    def running(self):
        return self.lock.locked()

    def start(self):
        if not self.lock.acquire(blocking=False):
            return False
        self.started = dt.datetime.now()
        threading.Thread(target=self._run, daemon=True).start()
        return True

    def _run(self):
        from . import fetch
        try:
            _, new, errors = fetch.run(commit=False)
            self.result = f"{len(new)} new adverts" + (f", {len(errors)} source errors (see latest-digest.md)" if errors else "")
        except Exception as e:
            self.result = f"failed: {e}"
        finally:
            self.finished = dt.datetime.now()
            self.lock.release()

    def last_fetch(self):
        """Time of the last completed fetch: this process's, else latest-digest.md's mtime (CLI runs)."""
        if self.finished:
            return self.finished
        digest = SEARCH / "latest-digest.md"
        return dt.datetime.fromtimestamp(digest.stat().st_mtime) if digest.exists() else None


def create_app():
    app = Flask(__name__)
    app.secret_key = secrets.token_hex(16)
    fetcher = Fetcher()
    drafter = Drafter()

    @app.before_request
    def csrf():
        session.setdefault("csrf", secrets.token_hex(16))
        if request.method == "POST" and request.form.get("csrf") != session["csrf"]:
            abort(400, "stale form - reload the page")

    @app.context_processor
    def ctx():
        try:
            repo = datarepo.status()
        except Exception as e:  # git missing/slow: the page must still render
            repo = {"ok": False, "changes": [], "branch": None, "ahead": 0, "error": str(e)}
        repo["message"] = datarepo.default_message(repo["changes"]) if repo["changes"] else ""
        return {"csrf": session.get("csrf"), "STATUSES": jobs.STATUSES, "data_dir": DATA, "repo": repo,
                "browser": app.config.get("BROWSER", False)}  # True in the browser version (web/bridge.py)

    @app.route("/")
    def inbox():
        status = request.args.get("status", "new,shortlisted,drafting")
        q = request.args.get("q", "").lower()
        show_blocked = request.args.get("blocked") == "1"
        wanted = set(status.split(",")) if status != "all" else None
        checker = blocklist.Checker()
        all_jobs = jobs.all_jobs()
        limit, area = jobs.min_salary(), jobs.commute_patterns()
        for j in all_jobs:  # salary/commute only hide adverts you haven't acted on yet
            fresh = j.get("status") in ("new", "skipped")
            j["blocked"] = checker.reason(j) or (fresh and (jobs.below_salary(j, limit) or jobs.outside_commute(j, area))) or None
        hidden = sum(1 for j in all_jobs if j["blocked"])
        visible = [j for j in all_jobs if show_blocked or not j["blocked"]]
        rows = [j for j in visible
                if (wanted is None or j.get("status") in wanted)
                and (not q or q in f"{j.get('company', '')} {j.get('title', '')} {j.get('location', '')} {j.get('source', '')}".lower())]
        order = {"drafting": 0, "shortlisted": 1}
        rows.sort(key=lambda j: (order.get(j.get("status"), 2), -int(j.get("score") or 0)))
        counts = {}
        for j in visible:
            counts[j.get("status", "new")] = counts.get(j.get("status", "new"), 0) + 1
        return render_template("inbox.html", rows=rows, status=status, q=q, counts=counts, fetcher=fetcher,
                               hidden=hidden, show_blocked=show_blocked)

    @app.post("/add")
    def add_advert():
        f = {k: request.form.get(k, "") for k in ("company", "title", "location", "url", "source", "text", "posted", "status")}
        if not f["company"].strip() or not f["title"].strip():
            flash("error: company and title are needed")
            return redirect(url_for("inbox"))
        job_id, what, warnings = jobs.add_advert(**{**f, "text": f["text"].replace("\r\n", "\n")})
        flash(f"{what}: {job_id}")
        for w in warnings:
            flash(f"error: note - {w}. It stays in your list because you added it yourself.")
        if not f["text"].strip():
            flash("error: no advert text yet - paste it under Advert text on this page before drafting a letter")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/fetch")
    def start_fetch():
        if not fetcher.start():
            flash("a fetch is already running")
        return redirect(url_for("inbox"))

    @app.post("/data/commit")
    def data_commit():
        ok, summary = datarepo.commit_and_push(request.form.get("message", ""))
        flash(summary if ok else f"error: {summary}")
        return redirect(request.form.get("next") or url_for("inbox"))

    @app.post("/job/<job_id>/status")
    def set_status(job_id):
        jobs.set_status(job_id, request.form["status"])
        if request.form["status"] in ("applied", "acknowledged") and efforts.ensure_application(jobs.load(job_id)["id"]):
            flash("application logged in Efforts (today, electronic) - edit it there if the date or method differ")
        return redirect(request.form.get("next") or url_for("job", job_id=job_id))

    @app.route("/job/<job_id>")
    def job(job_id):
        j = jobs.load(job_id)
        d = j["dir"]
        def read(name):
            return (d / name).read_text(encoding="utf-8") if (d / name).exists() else ""
        draft = read("letter.draft.md")
        profile = (jobs.split_front_matter(draft)[0].get("profile") if draft else None) or "ch"  # the letter's sender
        check_txt = read("letter.check.txt")
        files = sorted(((p.name, dt.datetime.fromtimestamp(p.stat().st_mtime)) for p in d.iterdir() if p.is_file()),
                       key=lambda f: f[1], reverse=True)
        history = [r for r in efforts.read() if r["job_id"] == job_id]
        snapshots = [n for n, _ in files if n.startswith("advert-") and n.endswith(".pdf")]
        letters = [(n, t) for n, t in files if "-CoverLetter-" in n and n.endswith(".pdf")]  # newest first
        draft_time = next((t for n, t in files if n == "letter.draft.md"), None)
        letter_pdf = letters[0] if letters else None
        stale = bool(letter_pdf and draft_time and draft_time > letter_pdf[1])
        mail = gmail.email(d)
        cv = gmail.cv_path(d)
        word = next(((n, t) for n, t in files if "-CoverLetter-" in n and n.endswith(".docx")), None)
        attached, problems = gmail.attachments(d)
        last_run = None  # survives restarts: the drafter's memory doesn't
        log = read("letter.claude.log")
        if log and not (drafter.job == j["id"] and (drafter.running or drafter.finished)):
            head, _, rest = log.partition("\n")
            summary = rest.strip().split("\n\n", 1)[-1] if "\n\n" in rest.strip() else rest.strip()
            last_run = {"at": head[:19], "summary": summary}
        via = None
        if j.get("via"):
            try:
                via = conversations.load(j["via"])
            except FileNotFoundError:
                via = None
        return render_template("job.html", j=j, via=via, last_run=last_run, emails=correspondence.emails(d), KINDS=correspondence.KINDS, notes=read("notes.md"), draft=draft, analysis=read("letter.analysis.md"),
                               drafter=drafter, letter_pdf=letter_pdf, letter_docx=word, stale=stale, mail=mail, cv=cv,
                               folder=d, file_names={n for n, _ in files}, profile=profile,
                               attached=[a.name for a in attached], problems=problems, gmail_status=gmail.status(),
                               check_txt=check_txt,
                               files=files, snapshots=snapshots, history=history, today=jobs.today(),
                               TYPES=efforts.TYPES, METHODS=efforts.METHODS, RESULTS=efforts.RESULTS)

    @app.post("/job/<job_id>/save/<what>")
    def save(job_id, what):
        d = jobs.job_dir(job_id)
        text = request.form["text"].replace("\r\n", "\n")
        if what == "notes":
            (d / "notes.md").write_text(text, encoding="utf-8")
        elif what == "draft":
            (d / "letter.draft.md").write_text(text, encoding="utf-8")
        elif what == "posting":
            meta, _ = jobs.split_front_matter((d / "posting.md").read_text(encoding="utf-8"))
            meta.pop("summary_only", None)
            if not meta.get("salary") and (found := jobs.extract_salary(text)):
                meta["salary"] = found
            (d / "posting.md").write_text(jobs.join_front_matter(meta, "\n" + text.strip() + "\n"), encoding="utf-8")
        else:
            abort(404)
        flash(f"saved {what}")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/check")
    def run_check(job_id):
        d = jobs.job_dir(job_id)
        if not (d / "letter.draft.md").exists():
            flash("no letter.draft.md yet")
        else:
            (d / "letter.check.txt").write_text(report_text(check(d)) + "\n")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/render")
    def run_render(job_id):
        from .render import build
        force = request.form.get("force") == "1"  # only from the user's own "render anyway" button
        outputs, result = build(job_id, force=force)
        if outputs and force and result["errors"]:
            d = jobs.job_dir(job_id)
            with (d / "letter.override.txt").open("a", encoding="utf-8") as f:
                f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} rendered despite {len(result['errors'])} check error(s), "
                        "acknowledged by the user in the web app:\n" + "".join(f"  {e}\n" for e in result["errors"]))
            flash(f"rendered anyway, despite {len(result['errors'])} error(s) - recorded in letter.override.txt: "
                  + ", ".join(o.name for o in outputs))
        else:
            flash("rendered " + ", ".join(o.name for o in outputs) if outputs else "not rendered - check failed")
        return redirect(url_for("job", job_id=job_id))

    @app.route("/job/<job_id>/cv")
    def job_cv(job_id):
        cv = gmail.cv_path(jobs.job_dir(job_id))
        if not cv or not cv.exists():
            abort(404)
        return send_from_directory(cv.parent, cv.name)

    @app.post("/job/<job_id>/cv")
    def job_cv_upload(job_id):
        f = request.files.get("file")
        try:
            rel, profile = gmail.save_cv(job_id, f.filename if f else "", f.read() if f else b"")
            flash(f"CV saved as {rel} and set as the '{profile}' sender's cv: in letter-rules.yaml - "
                  "every letter with that sender profile uses it")
        except ValueError as e:
            flash(f"error: {e}")
        return redirect(url_for("job", job_id=job_id) + "#quick-files")

    @app.post("/job/<job_id>/claude")
    def run_claude(job_id):
        j = jobs.load(job_id)
        try:
            started = drafter.start(j["id"], request.form.get("instructions", ""), revise=request.form.get("mode") == "revise")
        except RuntimeError as e:
            flash(f"error: {e}")
            return redirect(url_for("job", job_id=job_id))
        if not started:
            flash(f"Claude is still working on {drafter.job} - wait for it to finish")
        elif j.get("status") in ("new", "shortlisted"):
            jobs.set_status(j["id"], "drafting")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/email")
    def save_email(job_id):
        fields = {k: request.form.get(k, "").replace("\r\n", "\n") for k in ("to", "subject", "body")}
        if request.form.get("action") == "gmail":
            gmail.forget_status()
            ok, why = gmail.status()
            if not ok:
                gmail.save_email(job_id, **fields)
                flash(f"error: not saved to Gmail - {why}. Your edits are kept here.")
                return redirect(url_for("job", job_id=job_id))
            try:
                _, files = gmail.save_to_gmail(job_id, **fields)
                flash(f"saved to Gmail Drafts (not sent), attached: {', '.join(files)}")
            except SystemExit as e:  # not logged in
                flash(f"error: {e}")
            except Exception as e:
                gmail.forget_status()
                flash(f"error: Gmail: {e}")
        else:
            gmail.save_email(job_id, **fields)
            flash("email saved here (not in Gmail yet)")
        return redirect(url_for("job", job_id=job_id))

    @app.post("/job/<job_id>/snapshot")
    def take_snapshot(job_id):
        from .snapshot import take
        try:
            flash(f"saved {take(job_id).name}")
        except Exception as e:
            flash(f"error: snapshot failed: {e}")
        return redirect(url_for("job", job_id=job_id))

    @app.route("/job/<job_id>/file/<name>")
    def job_file(job_id, name):
        d = jobs.job_dir(job_id)
        if name not in {p.name for p in d.iterdir() if p.is_file()}:
            abort(404)
        return send_from_directory(d, name)

    @app.route("/efforts", methods=["GET", "POST"])
    def efforts_page():
        if request.method == "POST":
            errors = efforts.add({k: request.form.get(k, "") for k in efforts.FIELDS})
            for e in errors:
                flash("error: " + e)
            if not errors:
                flash("logged")
            return redirect(request.form.get("next") or url_for("efforts_page"))
        month = request.args.get("month", "")
        rows = [(i, r) for i, r in enumerate(efforts.read()) if r["date"].startswith(month)]
        open_jobs = [j for j in jobs.all_jobs() if j.get("status") in correspondence.OPEN_STATUSES]
        return render_template("efforts.html", rows=list(reversed(rows)), month=month, today=jobs.today(),
                               open_jobs=open_jobs, KINDS=correspondence.KINDS,
                               FIELDS=efforts.FIELDS, TYPES=efforts.TYPES, METHODS=efforts.METHODS,
                               RESULTS=efforts.RESULTS)

    @app.post("/emails")
    def paste_email():
        text, kind = request.form.get("text", ""), request.form.get("kind", "acknowledgement")
        job_id = request.form.get("job_id", "").strip()
        back = request.form.get("next") or url_for("efforts_page")
        if not job_id or job_id == "auto":
            guesses = correspondence.guess_jobs(text)
            if not guesses:
                flash("error: couldn't tell which application this email is about - choose the job and paste it again")
                return redirect(back)
            if len(guesses) > 1 and guesses[0][0] == guesses[1][0]:
                flash("error: it could be " + " or ".join(f"{g['company']} ({g['title']})" for _, g in guesses[:3])
                      + " - choose the job and paste it again")
                return redirect(back)
            job_id = guesses[0][1]["id"]
        try:
            summary, warnings = correspondence.save(job_id, text, kind, request.form.get("reason", ""),
                                                    request.form.get("message_id", ""))
        except (ValueError, SystemExit) as e:
            flash(f"error: {e}")
            return redirect(back)
        flash(summary)
        for w in warnings:
            flash(f"error: {w}")
        if request.form.get("message_id"):  # from the Gmail list: back there for the next one
            return redirect(back)
        return redirect(url_for("job", job_id=jobs.load(job_id)["id"]))

    @app.get("/emails/gmail")
    def gmail_emails():
        days = request.args.get("days", 30, type=int)
        found, error = [], ""
        ok, why = gmail.status()
        if not ok:
            error = why
        else:
            try:
                found = correspondence.gmail_candidates(days)
            except SystemExit as e:
                error = str(e)
            except Exception as e:
                error = f"couldn't read Gmail ({str(e)[:200]})"
        open_jobs = [j for j in jobs.all_jobs() if j.get("status") in correspondence.OPEN_STATUSES]
        return render_template("gmail_emails.html", found=found, error=error, days=days,
                               open_jobs=open_jobs, KINDS=correspondence.KINDS)

    @app.post("/emails/gmail/ignore")
    def gmail_ignore():
        correspondence.ignore(request.form.get("message_id", ""))
        flash("ignored - it won't be offered again")
        return redirect(request.form.get("next") or url_for("gmail_emails"))

    @app.post("/job/<job_id>/emails/<name>")
    def change_email(job_id, name):
        try:
            if request.form.get("action") == "remove":
                flash(correspondence.remove(job_id, name))
            else:
                summary, warnings = correspondence.reclassify(job_id, name, request.form.get("kind", ""),
                                                              request.form.get("reason", ""))
                flash(summary)
                for w in warnings:
                    flash(f"error: {w}")
        except (ValueError, SystemExit) as e:
            flash(f"error: {e}")
        return redirect(url_for("job", job_id=job_id) + "#emails")

    @app.route("/efforts/<int:idx>", methods=["GET", "POST"])
    def effort_edit(idx):
        rows = efforts.read()
        if not 0 <= idx < len(rows):
            abort(404)
        if request.method == "POST":
            if request.form.get("delete"):
                rows.pop(idx)
                efforts.write(rows)
                flash("deleted")
                return redirect(url_for("efforts_page"))
            row = {k: request.form.get(k, "").strip() for k in efforts.FIELDS}
            errors = efforts.validate(row)
            if errors:
                for e in errors:
                    flash("error: " + e)
            else:
                rows[idx] = row
                efforts.write(rows)
                flash("saved")
                return redirect(url_for("efforts_page"))
        return render_template("effort_edit.html", idx=idx, r=rows[idx], FIELDS=efforts.FIELDS,
                               TYPES=efforts.TYPES, METHODS=efforts.METHODS, RESULTS=efforts.RESULTS)

    @app.route("/report")
    def report():
        from .report import LANGS, applications, as_csv, month_label, rows_for
        month = request.args.get("month") or dt.date.today().strftime("%Y-%m")
        lang = request.args.get("lang") if request.args.get("lang") in LANGS else "it"
        if request.args.get("format") == "pdf":
            from .report import official_pdf
            path, _ = official_pdf(month)
            return send_from_directory(path.parent, path.name, mimetype="application/pdf",
                                       as_attachment=request.args.get("download") == "1")
        if request.args.get("format") == "csv":
            return Response(as_csv(month, lang), mimetype="text/csv",
                            headers={"Content-Disposition": f"attachment; filename=rav-{month}-{lang}.csv"})
        months = sorted({r["date"][:7] for r in efforts.read() if r["date"]}, reverse=True)
        from .report import insured
        return render_template("report.html", month=month, lang=lang, months=months, L=LANGS[lang], me=insured(),
                               month_label=month_label(month, lang),
                               rows=list(zip(rows_for(month, lang), [r["job_id"] for r, _, _ in applications(month)])))

    @app.route("/sources")
    def sources_page():
        edit = request.args.get("edit", type=int)
        counts = {}
        for j in jobs.all_jobs():
            counts[j.get("source", "")] = counts.get(j.get("source", ""), 0) + 1
        checker = blocklist.Checker()
        entries = sources.entries()
        blocked_boards = {e["board"] for e in entries["polled"]
                          if checker.source(e["board"]) or checker.company(e["name"])}
        hidden = {b["source"]: sum(n for src, n in counts.items() if blocklist.source_matches(b["source"], src))
                  for b in blocklist.load()["sources"]}
        return render_template("sources.html", entries=entries, counts=counts, edit=edit,
                               sites=sources.job_sites(counts), checker=checker, min_salary=jobs.min_salary(),
                               below=sum(1 for j in jobs.all_jobs() if jobs.below_salary(j)),
                               blocked_boards=blocked_boards, blocked_sources=blocklist.load()["sources"],
                               hidden=hidden)

    @app.post("/sources/block")
    def source_block():
        source = request.form["source"].strip()
        errors = blocklist.block_source(source, request.form.get("reason", ""))
        for e in errors:
            flash("error: " + e)
        if not errors:
            flash(f"blocked source {source}: not fetched, and its adverts are hidden from the inbox")
        return redirect(url_for("sources_page"))

    @app.post("/sources/unblock")
    def source_unblock():
        blocklist.unblock_source(request.form["source"])
        flash(f"unblocked {request.form['source']}")
        return redirect(url_for("sources_page"))

    @app.route("/companies")
    def companies_page():
        q = request.args.get("q", "").lower()
        checker = blocklist.Checker()
        by = {}  # normalized name -> row

        def row(name):
            key = blocklist.norm_company(name) or name.lower()
            r = by.setdefault(key, {"name": name, "names": set(), "statuses": {}, "adverts": 0,
                                    "sources": set(), "listed": "", "efforts": 0})
            r["names"].add(name)
            return r
        for j in jobs.all_jobs():
            r = row(j.get("company", "?"))
            r["adverts"] += 1
            r["statuses"][j.get("status", "new")] = r["statuses"].get(j.get("status", "new"), 0) + 1
            r["sources"].add((j.get("source") or "unknown").partition(":")[0])
        for section, items in sources.entries().items():
            for e in items:
                row(e["name"])["listed"] = section
        for e in efforts.read():
            row(e["company"])["efforts"] += 1
        blocked = blocklist.load()["companies"]
        rows = [r for r in by.values() if not checker.company(r["name"])
                and (not q or any(q in n.lower() for n in r["names"]))]
        rows.sort(key=lambda r: (-r["adverts"], r["name"].lower()))
        hidden = {b["name"]: sum(r["adverts"] for r in by.values() if checker.company(r["name"]) == b["name"])
                  for b in blocked}
        return render_template("companies.html", rows=rows, q=q, blocked=blocked, hidden=hidden)

    @app.post("/companies/block")
    def company_block():
        name = request.form["name"].strip()
        aliases = request.form.get("aliases", "").split(",")
        errors = blocklist.block_company(name, aliases, request.form.get("reason", ""))
        for e in errors:
            flash("error: " + e)
        if not errors:
            hidden = sum(1 for j in jobs.all_jobs() if blocklist.Checker().company(j.get("company")) )
            flash(f"blocked {name}: {hidden} advert{'s' if hidden != 1 else ''} hidden, from any source - undo on the Companies tab")
        return redirect(request.form.get("next") or url_for("companies_page"))

    @app.post("/companies/unblock")
    def company_unblock():
        blocklist.unblock_company(request.form["name"])
        flash(f"unblocked {request.form['name']}")
        return redirect(url_for("companies_page"))

    def entry_form():
        return {k: request.form.get(k, "").strip() for k in sources.KEYS}

    @app.post("/sources/add")
    def source_add():
        errors = sources.add(entry_form())
        for e in errors:
            flash("error: " + e)
        if not errors:
            flash(f"added {request.form['name']}")
        return redirect(request.form.get("next") or url_for("sources_page"))

    @app.post("/sources/<int:line>")
    def source_change(line):
        name = request.form["old_name"]
        try:
            if request.form.get("action") == "remove":
                sources.remove(line, name)
                flash(f"removed {name}")
            else:
                errors = sources.update(line, name, entry_form())
                for e in errors:
                    flash("error: " + e)
                if errors:
                    return redirect(url_for("sources_page", edit=line))
                flash(f"saved {request.form['name']}")
        except ValueError as e:
            flash(f"error: {e}")
        return redirect(url_for("sources_page"))

    @app.post("/sources/test")
    def source_test():
        board = request.form["board"]
        try:
            total, matching = sources.test_board(board)
            flash(f"{board}: {total} adverts on the board, {matching} pass your title/location filters")
        except Exception as e:
            flash(f"error: {board}: {e}")
        return redirect(url_for("sources_page"))

    @app.post("/sources/min-salary")
    def source_min_salary():
        try:
            value = int(request.form.get("min_salary", "0").replace("'", "").replace(",", "").strip() or 0)
            sources.set_min_salary(value)
            flash(f"minimum salary set to CHF {value:,}" if value else "minimum salary filter turned off")
        except ValueError as e:
            flash(f"error: {e}")
        return redirect(url_for("sources_page"))

    @app.post("/sources/site/<site>")
    def source_site(site):
        on = request.form.get("on") == "1"
        try:
            sources.set_site(site, on)
            flash(f"{site} " + ("switched on" if on else "switched off - adverts already fetched stay in the inbox"))
        except ValueError as e:
            flash(f"error: {e}")
        return redirect(url_for("sources_page"))

    @app.route("/recruiters")
    def recruiters():
        convs = conversations.all_conversations()
        titles = {j["id"]: j for j in jobs.all_jobs()}
        return render_template("recruiters.html", convs=convs, jobs_by_id=titles)

    @app.post("/recruiters/paste")
    def recruiter_paste():
        try:
            conv_id, total, new = conversations.save_paste(request.form.get("text", ""), request.form.get("agency", ""))
        except ValueError as e:
            flash(f"error: {e}")
            return redirect(url_for("recruiters"))
        flash(f"{total} messages, {new} new" if new else f"already saved - all {total} messages were known")
        return redirect(url_for("recruiter", conv_id=conv_id))

    @app.route("/recruiters/<conv_id>")
    def recruiter(conv_id):
        try:
            c = conversations.load(conv_id)
        except FileNotFoundError:
            abort(404)
        by_id = {j["id"]: j for j in jobs.all_jobs()}
        known_urls = {j.get("url"): j["id"] for j in by_id.values() if j.get("url")}
        contacts = [r for r in efforts.read() if r["type"] == "recruiter-contact" and conv_id in r["notes"]]
        return render_template("recruiter.html", c=c, by_id=by_id, known_urls=known_urls, contacts=contacts,
                               clean_url=jobs.clean_url, today=jobs.today(), METHODS=efforts.METHODS)

    @app.post("/recruiters/<conv_id>/edit")
    def recruiter_edit(conv_id):
        conversations.update(conv_id, **{k: request.form.get(k, "").strip() for k in ("contact", "email", "agency", "phone", "status", "notes")})
        flash("saved")
        return redirect(url_for("recruiter", conv_id=conv_id))

    @app.post("/recruiters/<conv_id>/add-job")
    def recruiter_add_job(conv_id):
        c = conversations.load(conv_id)
        f = {k: request.form.get(k, "").strip() for k in ("url", "company", "title", "location")}
        if not f["company"] or not f["title"]:
            flash("error: company and title are needed")
            return redirect(url_for("recruiter", conv_id=conv_id))
        job_id, what, warnings = jobs.add_advert(f["company"], f["title"], f["location"], f["url"],
                                                 f"recruiter:{jobs.slugify(c.get('agency') or c.get('contact') or 'direct', 20)}")
        conversations.link_job(conv_id, job_id)
        flash(f"{what} and linked to this conversation: {job_id}")
        for w in warnings:
            flash(f"error: note - {w}")
        return redirect(url_for("recruiter", conv_id=conv_id))

    @app.post("/recruiters/<conv_id>/link")
    def recruiter_link(conv_id):
        job_id = request.form.get("job_id", "").strip()
        try:
            conversations.link_job(conv_id, jobs.load(job_id)["id"])
            flash(f"linked {job_id}")
        except SystemExit:
            flash(f"error: no such job {job_id}")
        return redirect(url_for("recruiter", conv_id=conv_id))

    @app.post("/recruiters/<conv_id>/log-contact")
    def recruiter_log_contact(conv_id):
        c = conversations.load(conv_id)
        row = {"date": request.form.get("date") or jobs.today(), "type": "recruiter-contact",
               "company": c.get("agency") or c.get("contact"), "contact_person": c.get("contact", ""),
               "contact_email": c.get("email", ""), "contact_phone": c.get("phone", ""),
               "method": request.form.get("method", "electronic"), "result": "open",
               "notes": (request.form.get("notes", "").strip() + f" (conversation {conv_id})").strip()}
        errors = efforts.add(row)
        flash("; ".join(errors) if errors else f"logged recruiter contact on {row['date']}")
        return redirect(url_for("recruiter", conv_id=conv_id))

    @app.route("/facts")
    def facts():
        data = yaml.safe_load(FACTS.read_text(encoding="utf-8"))["facts"]
        return render_template("facts.html", facts=data)

    app.jinja_env.tests["search"] = lambda value, pattern: re.search(pattern, str(value)) is not None
    app.jinja_env.filters["filetime"] = lambda ts: dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
    app.jinja_env.filters["nl2br"] = lambda s: Markup("<br>".join(html.escape(x) for x in str(s).splitlines()))
    app.jinja_env.filters["linkify"] = lambda s: Markup(re.sub(r"(https?://\S+)", r'<a href="\1">\1</a>', str(html.escape(s))))
    return app
