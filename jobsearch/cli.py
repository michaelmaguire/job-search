"""jobsearch - command line entry point."""
import argparse
import sys
from pathlib import Path

from . import efforts, jobs


def _log_progress():
    """Fetch progress (which site is being checked) to stderr - the digest still goes to stdout."""
    import logging
    logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    logging.getLogger("jobsearch").setLevel(logging.INFO)


def cmd_fetch(a):
    _log_progress()
    from .fetch import run
    digest, _, errors = run(dry_run=a.dry_run, commit=a.commit)
    print(digest)
    return 1 if errors and a.strict else 0


def cmd_add_job(a):
    """Save an advert found elsewhere (LinkedIn, company site...). Text from --text-file or stdin."""
    text = Path(a.text_file).read_text(encoding="utf-8") if a.text_file else sys.stdin.read()
    job_id, what, warnings = jobs.add_advert(a.company, a.title, a.location, a.url, a.source, text, status=a.status)
    for w in [what] + warnings:
        print(w, file=sys.stderr)
    print(job_id)


def cmd_snapshot(a):
    from .snapshot import take
    print(take(a.job))


def cmd_status(a):
    jobs.set_status(a.job, a.status)
    if a.status in ("applied", "acknowledged") and (row := efforts.ensure_application(jobs.load(a.job)["id"])):
        print(f"logged application {row['date']} {row['company']} - {row['job_title']} in efforts.csv")


def cmd_list(a):
    for j in jobs.all_jobs():
        if a.status and j.get("status") != a.status:
            continue
        print(f"{j.get('status', ''):12} {str(j.get('score', '')):>3}  {j['id']}  |  {j.get('location', '')}")


def cmd_check(a):
    from .check import check, report_text
    result = check(a.job)
    text = report_text(result)
    (jobs.job_dir(a.job) / "letter.check.txt").write_text(text + "\n")
    print(text)
    return 1 if result["errors"] else 0


def cmd_render(a):
    from .check import report_text
    from .render import build
    outputs, result = build(a.job, force=a.force)
    print(report_text(result))
    if outputs is None:
        print("not rendered: fix the errors above (or --force)")
        return 1
    for o in outputs:
        print(o)


def cmd_log(a):
    row = {k: getattr(a, k) for k in efforts.FIELDS if getattr(a, k, None)}
    if a.job_id and not row.get("company"):
        j = jobs.load(a.job_id)
        row.setdefault("company", j.get("company", ""))
        row.setdefault("job_title", j.get("title", ""))
        row.setdefault("link", j.get("url", ""))
    errors = efforts.add(row)
    for e in errors:
        print("ERROR", e)
    return 1 if errors else 0


def cmd_report(a):
    from .report import write
    for p in write(a.month, name=a.name, lang=a.lang, pdf=a.pdf):
        print(p)


def cmd_gmail(a):
    from . import gmail
    if a.gmail_cmd == "login" and a.imap:
        import getpass
        address = a.address or input("Gmail address: ").strip()
        password = getpass.getpass("App password (https://myaccount.google.com/apppasswords): ")
        print("IMAP login works for", gmail.gmail_imap.login(address, password), "- saved to", gmail.gmail_imap.config_path())
    elif a.gmail_cmd == "login":
        print("logged in as", gmail.login(), "- token saved to", gmail.TOKEN)
    elif a.gmail_cmd == "list":
        for m in gmail.messages(a.query, a.max):
            print(f"{m['date'][:25]:25}  {m['from'][:35]:35}  {m['subject'][:70]}")
    elif a.gmail_cmd == "check":
        return check_gmail(a.days, a.max, a.confirm)
    elif a.gmail_cmd == "draft":
        e = gmail.email(a.job)
        body = open(a.body_file, encoding="utf-8").read() if a.body_file else e["body"]
        try:
            _, files = gmail.save_to_gmail(a.job, a.to, e["subject"], body, not a.no_letter, a.attach or [])
        except RuntimeError as err:
            raise SystemExit(str(err))
        print(f"Gmail draft saved (not sent): {e['subject']!r} to {a.to}, attached: {', '.join(files) or 'nothing'}")
        print("review and send it from Gmail > Drafts")


def check_gmail(days, limit, ask):
    """List Gmail replies to open applications; with ask=True, confirm each one to save it."""
    from . import correspondence
    found = correspondence.gmail_candidates(days, limit)
    if not found:
        print(f"no new emails about open applications in the last {days} days")
        return 0
    keys = {"a": "acknowledgement", "r": "rejection", "o": "other"}
    for n, c in enumerate(found, 1):
        best = c["guesses"][0][1] if c["guesses"] else None
        print(f"\n[{n}/{len(found)}] {c['date'][:25]}  {c['from']}\n    {c['subject']}")
        print(f"    looks like: {c['kind']} - " + (f"{best['company']}, {best['title']} ({best['status']})" if best else "job unknown"))
        if not ask:
            continue
        if not best:
            print("    choose the job in the web app (Efforts > Check Gmail), or paste it there")
            continue
        answer = input("    save? [y]es / as [a]ck, [r]ejection, [o]ther / [i]gnore for good / [s]kip: ").strip().lower()[:1]
        if answer == "i":
            correspondence.ignore(c["message_id"])
            print("    ignored")
        elif answer in ("y", *keys):
            summary, warnings = correspondence.save(best["id"], c["text"], keys.get(answer, c["kind"]),
                                                    message_id=c["message_id"])
            print("    " + summary)
            for w in warnings:
                print("    ERROR", w)
    if not ask:
        print("\nnothing saved - run with --confirm to go through them, or use Efforts > Check Gmail in the web app")
    return 0


def cmd_serve(a):
    _log_progress()
    from .web import create_app
    print(f"job search: http://127.0.0.1:{a.port}/")
    create_app().run(host="127.0.0.1", port=a.port, debug=False)


def main(argv=None):
    p = argparse.ArgumentParser(prog="jobsearch")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("fetch", help="poll job boards for new matching adverts")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--commit", action="store_true", help="git-commit new adverts in the data repo")
    s.add_argument("--strict", action="store_true", help="exit 1 if any source failed")
    s.set_defaults(func=cmd_fetch)

    s = sub.add_parser("add-job", help="save an advert found elsewhere (text on stdin or --text-file)")
    s.add_argument("--company", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--location", default="")
    s.add_argument("--url", default="")
    s.add_argument("--source", default="", help="default: from the URL (linkedin, jobs.ch, the site's domain)")
    s.add_argument("--text-file")
    s.add_argument("--status", default="shortlisted", choices=jobs.STATUSES)
    s.set_defaults(func=cmd_add_job)

    s = sub.add_parser("status", help="set a job's status")
    s.add_argument("job")
    s.add_argument("status", choices=jobs.STATUSES)
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("snapshot", help="save a PDF of the advert's web page (proof for the RAV)")
    s.add_argument("job")
    s.set_defaults(func=cmd_snapshot)

    s = sub.add_parser("list", help="list jobs")
    s.add_argument("--status", choices=jobs.STATUSES)
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("check", help="fact-check a job's letter.draft.md")
    s.add_argument("job")
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("render", help="check, then produce PDF + DOCX of the letter")
    s.add_argument("job")
    s.add_argument("--force", action="store_true", help="render even if the check fails")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("log", help="add a row to efforts.csv")
    for f in efforts.FIELDS:
        kw = {"choices": efforts.TYPES, "required": True} if f == "type" else \
             {"choices": efforts.METHODS} if f == "method" else \
             {"choices": efforts.RESULTS} if f == "result" else {}
        s.add_argument("--" + f.replace("_", "-"), dest=f, **kw)
    s.set_defaults(func=cmd_log)

    s = sub.add_parser("report", help="monthly RAV-style summary (YYYY-MM), in German or Italian")
    s.add_argument("month")
    s.add_argument("--lang", choices=["it", "de"], default="it", help="form language (URC it, the default / RAV de)")
    s.add_argument("--name", default="", help="default: name in ~/.config/jobsearch/insured.yaml")
    s.add_argument("--pdf", action="store_true", help="it: fill the official form 716.007 i; de: a PDF in the same layout")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("gmail", help="Gmail: login, list messages, create application drafts (never sends)")
    g = s.add_subparsers(dest="gmail_cmd", required=True)
    gi = g.add_parser("login", help="sign in in the browser (stores a token in ~/.config/jobsearch)")
    gi.add_argument("--imap", action="store_true", help="use IMAP with an app password instead of OAuth (used whenever set up)")
    gi.add_argument("--address", help="with --imap: the Gmail address (asked for if left out)")
    gl = g.add_parser("list", help="list recent messages")
    gl.add_argument("--query", default="", help="Gmail search, e.g. 'from:jobs.ch newer_than:7d'")
    gl.add_argument("--max", type=int, default=20)
    gc = g.add_parser("check", help="find acknowledgements/rejections for open applications (saves only what you confirm)")
    gc.add_argument("--days", type=int, default=30, help="how far back to look (default 30)")
    gc.add_argument("--max", type=int, default=50, help="most messages to read (default 50)")
    gc.add_argument("--confirm", action="store_true", help="go through them one by one and save the ones you confirm")
    gd = g.add_parser("draft", help="create a draft application email for a job (letter PDF + CV attached)")
    gd.add_argument("job")
    gd.add_argument("--to", required=True)
    gd.add_argument("--body-file", help="email body (default: a short covering note)")
    gd.add_argument("--no-letter", action="store_true", help="don't require/attach the rendered letter")
    gd.add_argument("--attach", action="append", help="extra file to attach (repeatable)")
    s.set_defaults(func=cmd_gmail)

    s = sub.add_parser("serve", help="run the local web app")
    s.add_argument("--port", type=int, default=8765)
    s.set_defaults(func=cmd_serve)

    a = p.parse_args(argv)
    if a.cmd == "log" and not a.date:
        a.date = jobs.today()
    sys.exit(a.func(a) or 0)


if __name__ == "__main__":
    main()
