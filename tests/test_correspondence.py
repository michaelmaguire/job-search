"""Pasting acknowledgement / rejection emails for applications."""
import os
import shutil
import tempfile
import unittest

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import correspondence, efforts, jobs, paths, web  # noqa: E402  (must follow JOBSEARCH_DATA)

ACK = """From: Example Exchange Recruiting <noreply@example-exchange.ch>
Date: 16.10.2026 09:12

Dear Jane, thank you for your application as Senior Software Engineer (80-100%). We have received it and will be in touch.
"""
REJECT = """On Friday, 10/23/26 at 10:00 Talent Team <talent@example-exchange.ch> wrote:
Dear Jane, unfortunately we have decided to continue with other candidates for the Senior Software Engineer role.
"""


class Correspondence(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(paths.JOBS, ignore_errors=True)
        self.six = jobs.create_job("Example Exchange AG", "Senior Software Engineer (80-100%)", "Zurich", "https://jobs.example-exchange.ch/job/x/1/",
                                   "successfactors:jobs.example-exchange.ch", "advert")
        self.other = jobs.create_job("Acme AG", "Platform Engineer", "Zug", "https://acme.example/job", "manual", "advert")
        efforts.write([r for r in efforts.read() if r["job_id"] not in (self.six, self.other)])  # ids carry today's date
        for j in (self.six, self.other):
            jobs.set_status(j, "applied")
            efforts.ensure_application(j)
        self.client = web.create_app().test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.csrf = s["csrf"]

    def paste(self, text, **form):
        return self.client.post("/emails", data={"csrf": self.csrf, "text": text, "job_id": "auto", "kind": "acknowledgement", **form},
                                follow_redirects=True).get_data(as_text=True)

    def test_guess_prefers_matching_company_and_domain(self):
        self.assertEqual(correspondence.guess_jobs(ACK)[0][1]["id"], self.six)

    def test_acknowledgement_sets_status_and_keeps_email(self):
        page = self.paste(ACK)
        self.assertIn("job is now acknowledged", page)
        self.assertEqual(jobs.load(self.six)["status"], "acknowledged")
        saved = correspondence.emails(self.six)
        self.assertEqual((saved[0]["kind"], saved[0]["received"], saved[0]["from"]),
                         ("acknowledgement", "2026-10-16", "Example Exchange Recruiting <noreply@example-exchange.ch>"))
        self.assertIn("received it", saved[0]["text"])
        self.assertIn("already saved", self.paste(ACK))  # pasting the same email again
        self.assertEqual(len(correspondence.emails(self.six)), 1)

    def test_rejection_logs_result_with_reason(self):
        self.paste(REJECT, kind="rejection")
        self.assertEqual(jobs.load(self.six)["status"], "rejected")
        row = [r for r in efforts.read() if r["job_id"] == self.six and r["type"] == "result"][0]
        self.assertEqual((row["date"], row["result"]), ("2026-10-23", "rejected"))
        self.assertIn("continue with other candidates", row["rejection_reason"])
        app = [r for r in efforts.read() if r["job_id"] == self.six and r["type"] == "application"][0]
        self.assertEqual(efforts.latest_result(efforts.read(), app)[0], "rejected")  # shows on the monthly form

    def change(self, name, **form):
        return self.client.post(f"/job/{self.six}/emails/{name}", data={"csrf": self.csrf, **form},
                                follow_redirects=True).get_data(as_text=True)

    def results(self):
        return [r for r in efforts.read() if r["job_id"] == self.six and r["type"] == "result"]

    def test_reclassify_acknowledgement_as_rejection_and_back(self):
        self.paste(ACK)
        page = self.change("2026-10-16-acknowledgement.md", action="reclassify", kind="rejection", reason="role filled")
        self.assertIn("rejection from Example Exchange AG saved", page)
        self.assertEqual([m["file"] for m in correspondence.emails(self.six)], ["2026-10-16-rejection.md"])
        self.assertEqual(jobs.load(self.six)["status"], "rejected")
        self.assertEqual([r["rejection_reason"] for r in self.results()], ["role filled"])
        self.change("2026-10-16-rejection.md", action="reclassify", kind="acknowledgement")
        self.assertEqual(self.results(), [])  # the rejection's result row goes with it
        self.assertEqual(jobs.load(self.six)["status"], "acknowledged")

    def test_remove_undoes_status_and_result(self):
        self.paste(REJECT, kind="rejection")
        self.assertEqual(len(self.results()), 1)
        page = self.change("2026-10-23-rejection.md", action="remove")
        self.assertIn("job is back to applied", page)
        self.assertEqual((correspondence.emails(self.six), self.results()), ([], []))
        self.assertEqual(jobs.load(self.six)["status"], "applied")
        self.paste(ACK)
        self.change("2026-10-16-acknowledgement.md", action="remove")
        self.assertEqual(jobs.load(self.six)["status"], "applied")

    def test_change_only_touches_this_jobs_emails(self):
        self.client.post(f"/job/{self.six}/emails/..%2F..%2Fposting.md", data={"csrf": self.csrf, "action": "remove"})
        self.assertIn("no saved email", self.change("nope.md", action="remove"))
        self.assertTrue((jobs.job_dir(self.six) / "posting.md").exists())

    def test_unclear_email_asks_for_the_job(self):
        page = self.paste("Thanks for applying! We'll get back to you.")
        self.assertIn("choose the job", page)
        page = self.paste("Thanks for applying! We'll get back to you.", job_id=self.other)
        self.assertIn("Acme AG", page)
        self.assertEqual(jobs.load(self.other)["status"], "acknowledged")


if __name__ == "__main__":
    unittest.main()
