"""Draft-with-Claude runner (fake claude CLI) and the job page's email card (Gmail mocked)."""
import os
import stat
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import drafter, gmail, jobs, web  # noqa: E402  (must follow JOBSEARCH_DATA)

# Writes the files a real run would, and prints stream-json events like `claude -p --output-format stream-json`.
FAKE_CLAUDE = r"""#!/bin/sh
dir="$JOBSEARCH_DATA/jobs/$FAKE_JOB"
echo "args: $*" > "$dir/fake-args.txt"
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Read","input":{"file_path":"data/profile/facts.yaml"}}]}}'
printf '| Requirement | Facts | Verdict |\n' > "$dir/letter.analysis.md"
printf -- '---\nto: X\n---\nDear Hiring Team,\n\nKind regards,\n' > "$dir/letter.draft.md"
echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Bash","input":{"command":".venv/bin/jobsearch check x"}}]}}'
echo '{"type":"result","result":"PASS. Gaps: SIMD.","is_error":false,"total_cost_usd":0.12}'
"""


class DraftWithClaude(unittest.TestCase):
    def setUp(self):
        self.job = jobs.create_job("Fake AG", "Engineer", "Zurich", "https://example.com/j", "manual", "C++ advert")
        self.bin = Path(tempfile.mkdtemp()) / "claude"
        self.bin.write_text(FAKE_CLAUDE)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        self.env = mock.patch.dict(os.environ, {"JOBSEARCH_CLAUDE": str(self.bin), "FAKE_JOB": self.job})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    def wait(self, d):
        for _ in range(100):
            if not d.running:
                return
            time.sleep(0.05)
        self.fail("drafter did not finish")

    def test_run_collects_progress_and_summary(self):
        d = drafter.Drafter()
        self.assertTrue(d.start(self.job, "stress C++"))
        self.wait(d)
        self.assertEqual(d.error, "")
        self.assertEqual(d.summary, "PASS. Gaps: SIMD.")
        self.assertEqual(d.cost, 0.12)
        self.assertEqual(d.progress, ["read data/profile/facts.yaml", "ran .venv/bin/jobsearch check x"])
        folder = jobs.job_dir(self.job)
        self.assertTrue((folder / "letter.analysis.md").exists())
        args = (folder / "fake-args.txt").read_text()
        self.assertIn("stress C++", args)
        self.assertIn(f"Write(/{folder}/**)", args)  # writes limited to this job's folder
        self.assertIn("--strict-mcp-config", args)
        self.assertIn("--permission-mode dontAsk", args)  # nothing beyond the allowed list

    def test_fresh_run_keeps_previous_draft_and_revise_needs_one(self):
        d = drafter.Drafter()
        with self.assertRaises(RuntimeError):
            d.start(self.job, "shorter", revise=True)
        self.assertFalse(d.running)
        (jobs.job_dir(self.job) / "letter.draft.md").write_text("old draft")
        d.start(self.job)
        self.wait(d)
        self.assertEqual((jobs.job_dir(self.job) / "letter.draft.prev.md").read_text(), "old draft")

    def test_one_run_at_a_time(self):
        d = drafter.Drafter()
        d.lock.acquire()
        self.assertFalse(d.start(self.job))
        d.lock.release()


class EmailCard(unittest.TestCase):
    def setUp(self):
        self.job = jobs.create_job("Mail AG", "Engineer", "Zurich", "https://example.com/m", "manual", "advert")
        folder = jobs.job_dir(self.job)
        (folder / "2026-10-15-YourName-CoverLetter-mail-ag.pdf").write_bytes(b"%PDF-1.4 test")
        self.cv = mock.patch.object(gmail, "attachments", lambda job, *a, **k: (
            [folder / "2026-10-15-YourName-CoverLetter-mail-ag.pdf"], []))
        self.cv.start()

    def tearDown(self):
        self.cv.stop()

    def test_save_to_gmail_updates_the_same_draft(self):
        calls = []

        def fake_create(to, subject, body, files, draft_id=None):
            calls.append(draft_id)
            return draft_id or "d1"
        with mock.patch.object(gmail, "create_draft", fake_create):
            gmail.save_to_gmail(self.job, "jobs@mail.example", "Application", "Hello")
            gmail.save_to_gmail(self.job, "jobs@mail.example", "Application", "Hello again")
        self.assertEqual(calls, [None, "d1"])
        saved = gmail.email(self.job)
        self.assertEqual((saved["body"], saved["gmail_draft_id"]), ("Hello again", "d1"))

    def test_job_page_shows_pdf_and_email(self):
        page = web.create_app().test_client().get(f"/job/{self.job}").get_data(as_text=True)
        self.assertIn("<iframe", page)
        self.assertIn("CoverLetter-mail-ag.pdf", page)
        self.assertIn("Save draft to Gmail", page)
        self.assertIn("Gmail isn&#39;t connected", page)  # no token in the test config dir
        self.assertIn("Draft with Claude", page)


class RenderAnyway(unittest.TestCase):
    def setUp(self):
        self.job = jobs.create_job("Force AG", "Engineer", "Zurich", "https://example.com/f", "manual", "advert")
        (jobs.job_dir(self.job) / "letter.draft.md").write_text("---\nto: X\n---\nDear Hiring Team,\n\nI am amazing.\n\nKind regards,\n")
        self.client = web.create_app().test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.csrf = s["csrf"]

    def fake_build(self, job, force=False):
        result = {"errors": ["UNTAGGED  line 5"], "warnings": [], "cited": [], "words": 3}
        return ([jobs.job_dir(job) / "x.pdf"] if force else None), result

    def test_button_only_after_failed_check_and_override_is_recorded(self):
        from jobsearch import render
        self.assertNotIn("render anyway", self.client.get(f"/job/{self.job}").get_data(as_text=True))
        (jobs.job_dir(self.job) / "letter.check.txt").write_text("ERROR  UNTAGGED  line 5\nFAIL (1 errors)\n")
        self.assertIn("render anyway", self.client.get(f"/job/{self.job}").get_data(as_text=True))
        with mock.patch.object(render, "build", self.fake_build):
            self.client.post(f"/job/{self.job}/render", data={"csrf": self.csrf})  # normal button: refused
            self.assertFalse((jobs.job_dir(self.job) / "letter.override.txt").exists())
            self.client.post(f"/job/{self.job}/render", data={"csrf": self.csrf, "force": "1"})
        log = (jobs.job_dir(self.job) / "letter.override.txt").read_text()
        self.assertIn("acknowledged by the user", log)
        self.assertIn("UNTAGGED  line 5", log)


class AppliedLogsEffort(unittest.TestCase):
    def test_marking_applied_logs_one_application(self):
        from jobsearch import efforts
        job = jobs.create_job("Applied AG", "Engineer", "Zug", "https://example.com/a", "manual", "advert")
        client = web.create_app().test_client()
        client.get("/")
        with client.session_transaction() as s:
            csrf = s["csrf"]
        for _ in range(2):  # a second click must not log a duplicate
            client.post(f"/job/{job}/status", data={"csrf": csrf, "status": "applied"})
        rows = [r for r in efforts.read() if r["job_id"] == job]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["type"], rows[0]["method"], rows[0]["result"], rows[0]["link"]),
                         ("application", "electronic", "open", "https://example.com/a"))


if __name__ == "__main__":
    unittest.main()
