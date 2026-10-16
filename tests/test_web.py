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
            calls.append(kw)
            release.wait(5)
            return "", [{"id": "x"}], []

        with mock.patch.object(fetch, "run", fake_run):
            self.client.post("/fetch", data={"csrf": self.csrf})
            page = self.client.get("/").get_data(as_text=True)
            self.assertIn('http-equiv="refresh"', page)
            self.assertIn("Fetching since", page)
            second = self.client.post("/fetch", data={"csrf": self.csrf}, follow_redirects=True)
            self.assertIn("already running", second.get_data(as_text=True))
            release.set()
            for t in threading.enumerate():
                if t is not threading.current_thread() and t.daemon:
                    t.join(5)
        self.assertEqual(calls, [{"commit": False}])
        page = self.client.get("/").get_data(as_text=True)
        self.assertNotIn('http-equiv="refresh"', page)
        self.assertIn("1 new adverts", page)


if __name__ == "__main__":
    unittest.main()
