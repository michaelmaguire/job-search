"""Job-alert emails (fake Gmail) as a fetch source: parsing, clean links, filters, no duplicates."""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import alerts, fetch, jobs, paths  # noqa: E402  (must follow JOBSEARCH_DATA)

CONFIG = """sources:
  linkedin_alerts: true
filters: {title_include: [engineer], title_exclude: [], locations: ["z[uü]rich", switzerland]}
score_terms: {}
min_score: 2
"""
TOKEN = "?trackingId=abc%3D%3D&refId=x&midToken=SECRET&otpToken=SECRET2"
DIGEST = f"""Your job alert for Software Engineer in Zurich
2 new jobs match your preferences.

Senior Platform Engineer
Acme AG
Zurich, Switzerland

3 connections
View job: https://www.linkedin.com/comm/jobs/view/1111111111/{TOKEN}

---------------------------------------------------------

Barista
Café Example
Zurich
This company is actively hiring
View job: https://www.linkedin.com/comm/jobs/view/2222222222/{TOKEN}

---------------------------------------------------------

Backend Engineer (80-100%)
Example Exchange
Zürich Metropolitan Area
Apply with resume & profile
View job: https://www.linkedin.com/comm/jobs/view/3333333333/{TOKEN}

---------------------------------------------------------

See all jobs on LinkedIn: https://www.linkedin.com/comm/jobs/search-results/?keywords=x
This email was intended for Jane Example
Unsubscribe: https://www.linkedin.com/job-alert-email-unsubscribe?x=y
"""
SINGLE = f"""Your job alert for Software Engineer in Zurich
1 new job matches your preferences.

Senior Platform Engineer
Acme AG
Zurich, Switzerland
View job: https://www.linkedin.com/comm/jobs/view/1111111111/{TOKEN}

---------------------------------------------------------
"""


def email(body, date, mid):
    return {"id": mid, "message_id": mid, "date": date, "from": "LinkedIn Job Alerts <jobalerts-noreply@linkedin.com>",
            "subject": "Jobs for you", "snippet": "", "body": body}


INBOX = [email(DIGEST, "Tue, 20 Oct 2026 09:18:00 +0200", "<2@x>"),  # newest first, like Gmail
         email(SINGLE, "Sat, 17 Oct 2026 09:19:00 +0200", "<1@x>")]


class LinkedInAlerts(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(paths.DATA, paths.REPO / "data", "tests must not touch the real data repo")
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text(CONFIG)
        for p in (paths.SEARCH / "blocklist.yaml", paths.SEARCH / "seen.txt"):
            p.unlink(missing_ok=True)
        shutil.rmtree(paths.JOBS, ignore_errors=True)
        self.queries = []
        self.patch = mock.patch.dict(fetch.FETCHERS, {"linkedin_alerts": alerts.fetcher("linkedin_alerts", self.fake_gmail)})
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def fake_gmail(self, query, limit, full=False):
        self.queries.append(query)
        return INBOX

    def test_parse_digest(self):
        found = alerts.parse_linkedin(INBOX[0])
        self.assertEqual([(j["title"], j["company"], j["location"]) for j in found], [
            ("Senior Platform Engineer", "Acme AG", "Zurich, Switzerland"),
            ("Barista", "Café Example", "Zurich"),
            ("Backend Engineer (80-100%)", "Example Exchange", "Zürich Metropolitan Area")])
        self.assertEqual(found[0]["url"], "https://www.linkedin.com/jobs/view/1111111111/")  # no tracking or sign-in tokens
        self.assertTrue(all("SECRET" not in str(j) for j in found))
        self.assertEqual((found[0]["posted"], found[0]["extra"]), ("2026-10-20", {"summary_only": True}))
        self.assertIn("3 connections", found[0]["text"])

    def test_fetch_saves_matching_jobs_once_dated_by_the_first_email(self):
        _, new, errors = fetch.run()
        self.assertEqual(errors, [])
        self.assertEqual(sorted(j["company"] for j in new), ["Acme AG", "Example Exchange"])  # the barista is filtered out
        self.assertIn("from:jobalerts-noreply@linkedin.com", self.queries[0])
        acme = next(j for j in jobs.all_jobs() if j["company"] == "Acme AG")
        self.assertEqual((acme["source"], acme["posted"], acme.get("summary_only")),
                         ("linkedin_alerts:linkedin_alerts", "2026-10-17", True))
        self.assertEqual(fetch.run()[1], [])  # the next fetch finds nothing new

    def test_alert_jobs_get_the_score_bonus(self):
        new = fetch.run(dry_run=True)[1]  # titles score 0 here: only the bonus gets them past min_score 2
        self.assertEqual(sorted((j["company"], j["score"]) for j in new), [("Acme AG", 2), ("Example Exchange", 2)])
        (paths.SEARCH / "config.yaml").write_text(CONFIG + "alert_score_bonus: 0\n")
        self.assertEqual(fetch.run(dry_run=True)[1], [])
        self.assertEqual(alerts.score_bonus({}, "swissdevjobs"), 0)

    def test_job_saved_by_hand_from_a_tracking_link_is_not_fetched_again(self):
        jobs.create_job("Acme AG", "Senior Platform Engineer", "Zurich", "https://www.linkedin.com/comm/jobs/view/1111111111" + TOKEN,
                        "linkedin", "advert text")
        self.assertEqual([j["company"] for j in fetch.run()[1]], ["Example Exchange"])

    def test_progress_is_reported_per_source(self):
        events = []
        with self.assertLogs("jobsearch.fetch", "INFO") as logs:
            fetch.run(dry_run=True, progress=events.append)
        self.assertEqual([(e["status"], e["label"]) for e in events],
                         [("checking", "LinkedIn job alerts (Gmail)"), ("done", "LinkedIn job alerts (Gmail)")])
        self.assertEqual((events[1]["listed"], events[1]["new"]), (3, 2))
        self.assertIn("[1/1] checking LinkedIn job alerts (Gmail)", "\n".join(logs.output))

    def test_gmail_not_connected_is_this_sources_error_only(self):
        def not_logged_in(*a, **k):
            raise SystemExit("not logged in to Gmail - run: jobsearch gmail login")
        with mock.patch.dict(fetch.FETCHERS, {"linkedin_alerts": alerts.fetcher("linkedin_alerts", not_logged_in)}):
            _, new, errors = fetch.run(dry_run=True)
        self.assertEqual(new, [])
        self.assertEqual(errors, ["linkedin_alerts:linkedin_alerts: not logged in to Gmail - run: jobsearch gmail login"])


if __name__ == "__main__":
    unittest.main()
