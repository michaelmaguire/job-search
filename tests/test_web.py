"""Web app tests (fetch button). Run: .venv/bin/python -m unittest discover tests"""
import os
import tempfile
import threading
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import fetch, web  # noqa: E402  (must follow JOBSEARCH_DATA)


class FetchButton(unittest.TestCase):
    def setUp(self):
        self.app = web.create_app()
        self.app.config["TESTING"] = True
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.csrf = s["csrf"]

    def test_one_fetch_at_a_time_and_no_commit(self):
        release, calls = threading.Event(), []

        def fake_run(**kw):
            calls.append({k: v for k, v in kw.items() if k != "progress"})
            kw["progress"]({"step": 1, "total": 2, "label": "Acme (greenhouse:acme)", "status": "done", "listed": 5, "new": 1})
            kw["progress"]({"step": 2, "total": 2, "label": "Broken (lever:broken)", "status": "checking"})
            release.wait(5)
            return "", [{"id": "x"}], []

        with mock.patch.object(fetch, "run", fake_run):
            self.client.post("/fetch", data={"csrf": self.csrf})
            page = self.client.get("/").get_data(as_text=True)
            self.assertIn('id="fetch-panel"', page)
            self.assertIn("Fetching since", page)
            self.assertIn('id="fetch-panel"', self.client.get("/efforts").get_data(as_text=True))  # on every page
            st = self.client.get("/fetch/status").get_json()
            self.assertEqual((st["running"], st["step"], st["total"], st["current"], st["new"]),
                             (True, 2, 2, "Broken (lever:broken)", 1))
            self.assertEqual(st["log"], [{"n": 1, "label": "Acme (greenhouse:acme)", "status": "done", "listed": 5,
                                          "new": 1, "error": None}])
            second = self.client.post("/fetch", data={"csrf": self.csrf}, follow_redirects=True)
            self.assertIn("already running", second.get_data(as_text=True))
            release.set()
            for t in threading.enumerate():
                if t is not threading.current_thread() and t.daemon:
                    t.join(5)
        self.assertEqual(calls, [{"commit": False}])
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('id="fetch-panel"', page)
        self.assertIn("1 new adverts", page)
        self.assertFalse(self.client.get("/fetch/status").get_json()["running"])


class StaleForms(unittest.TestCase):
    def test_restarted_server_accepts_pages_opened_before(self):
        before = web.create_app().test_client()
        before.get("/")
        with before.session_transaction() as s:
            csrf = s["csrf"]
        after = web.create_app().test_client()  # a restart: same key, so the old cookie and token still work
        after.set_cookie("session", before.get_cookie("session").value)
        r = after.post("/fetch/none", data={"csrf": csrf})
        self.assertEqual(r.status_code, 404)  # got past the form check
        from jobsearch.gmail import CONFIG
        self.assertEqual(oct((CONFIG / "web-secret").stat().st_mode & 0o777), "0o600")  # private, like the Gmail login

    def test_out_of_date_form_goes_back_with_a_message(self):
        client = web.create_app().test_client()
        client.get("/")
        r = client.post("/efforts", data={"csrf": "old"}, headers={"Referer": "http://localhost/efforts?month=2026-10"})
        self.assertEqual((r.status_code, r.location), (302, "http://localhost/efforts?month=2026-10"))
        page = client.get("/efforts").get_data(as_text=True)
        self.assertIn("out of date, so nothing was done", page)
        r = client.post("/efforts", data={"csrf": "old"}, headers={"Referer": "https://elsewhere.example/"})
        self.assertEqual(r.location, "/")  # never back to another site


if __name__ == "__main__":
    unittest.main()
