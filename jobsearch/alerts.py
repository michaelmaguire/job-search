"""Job-alert emails in Gmail as a job source: adverts that only reach you by email (LinkedIn has no
API and scraping it gets accounts banned, so its alert emails are the way in).

Each alert type is a Gmail search plus a parser that turns one email into jobs. Every type is a job
site in fetch.JOB_SITES (switched on/off on the Sources page, like swissdevjobs), so its jobs go
through the same filters, blocklist, scoring and seen-list as everything else fetched.

To add an alert type: write parse_<name>(msg) returning job dicts (company, title, location, url,
posted, text, extra) and add an entry to ALERTS. Keep only a clean link to the advert - links in
these emails carry tracking and sign-in tokens.

Never fetch the advert pages these emails link to (LinkedIn's especially): that is scraping. The
jobs stay summaries until you paste the advert. Because you chose what the alerts send you, their
jobs get a score bonus (`alert_score_bonus` in config.yaml, default SCORE_BONUS) - with only a title
to score, they'd otherwise fall under min_score.

Email contents are untrusted data: parsed into fields, never acted on. Needs the local app (Gmail).
"""
import re
from email.utils import parsedate_to_datetime

DAYS = 14        # how far back each fetch looks; already-seen adverts are skipped anyway
MAX_EMAILS = 50  # per alert type and fetch
SCORE_BONUS = 2  # added to an alert job's score unless config.yaml sets alert_score_bonus


def score_bonus(cfg, source):
    """Extra score for a job from an alert type (0 for every other source)."""
    return int(cfg.get("alert_score_bonus", SCORE_BONUS)) if source in ALERTS else 0

SUMMARY_NOTE = ("({site} alert emails only give the title, company and location - open the URL and paste the "
                "full advert below before writing a letter.)")


def _posted(msg):
    try:
        return parsedate_to_datetime(msg["date"]).date().isoformat()
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- LinkedIn
LINKEDIN_JOB = re.compile(r"View job:\s*https?://(?:[\w-]+\.)?linkedin\.com/(?:comm/)?jobs/view/(\d+)")
LINKEDIN_HEADER = re.compile(r"^(your job alert for|\d*\s*new jobs? match)", re.I)
LINKEDIN_NOTES = re.compile(r"^(\d+ connections?|this company is actively hiring|apply with resume.*|easy apply|"
                            r"actively recruiting|promoted|\d+ (?:school )?alum(?:ni|nus)?.*)$", re.I)


def parse_linkedin(msg):
    """Jobs in a LinkedIn job-alert email (single job or digest). Blocks end in 'View job: <link>';
    the first lines of a block are title, company, location."""
    out, start = [], 0
    body = msg.get("body", "")
    for m in LINKEDIN_JOB.finditer(body):
        block = body[start:m.start()]
        start = m.end()
        block = re.split(r"^\s*-{5,}\s*$", block, flags=re.M)[-1]  # the text since the previous job's separator line
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        lines = [line for line in lines if not LINKEDIN_HEADER.match(line)]
        fields = [line for line in lines if not LINKEDIN_NOTES.match(line)]
        if len(fields) < 2:
            continue
        title, company = fields[0], fields[1]
        location = fields[2] if len(fields) > 2 else ""
        notes = [line for line in lines if LINKEDIN_NOTES.match(line)]
        text = SUMMARY_NOTE.format(site="LinkedIn") + "\n\n" + "".join(f"- {n}\n" for n in notes)
        out.append(dict(company=company, title=title, location=location,
                        url=f"https://www.linkedin.com/jobs/view/{m.group(1)}/", posted=_posted(msg),
                        text=text, extra={"summary_only": True}))
    return out


# ---------------------------------------------------------------- registry
ALERTS = {
    "linkedin_alerts": {
        "name": "LinkedIn job alerts (Gmail)", "url": "https://www.linkedin.com/jobs/alerts/",
        "about": "Jobs from LinkedIn's job-alert emails in your Gmail - set the alerts up on LinkedIn. Summaries "
                 "only: paste the full advert before writing a letter. Needs the local app with Gmail connected.",
        "query": "from:jobalerts-noreply@linkedin.com",
        "parse": parse_linkedin,
    },
}


def fetcher(key, fetch=None):
    """A fetch.FETCHERS function for alert type `key`. `fetch` is gmail.messages (passed in by tests)."""
    alert = ALERTS[key]

    def run(_board):
        get = fetch
        if get is None:
            from .gmail import messages as get
        try:
            emails = get(f"{alert['query']} newer_than:{DAYS}d", MAX_EMAILS, full=True)
        except SystemExit as e:  # not logged in etc. - report it as this source's error, don't stop the fetch
            raise RuntimeError(str(e)) from None
        except ImportError:  # the browser version: no Gmail libraries
            raise RuntimeError("job-alert emails need the local app with Gmail connected") from None
        found = {}
        for msg in reversed(emails):  # oldest first, so a job keeps the date it was first announced
            for job in alert["parse"](msg):
                found.setdefault(job["url"], job)
        yield from found.values()

    return run
