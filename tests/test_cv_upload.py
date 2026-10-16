"""Uploading a CV from a job page (saved in raw/, set as the sender's cv:), and Add a company on the Companies tab."""
import os
import shutil
import tempfile
import unittest
from io import BytesIO
from pathlib import Path

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

import yaml  # noqa: E402

from jobsearch import gmail, jobs, paths, web  # noqa: E402  (must follow JOBSEARCH_DATA)

TEMPLATE = Path(__file__).resolve().parent.parent / "examples" / "data-template"


class CvUpload(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(paths.JOBS, ignore_errors=True)
        shutil.rmtree(paths.DATA / "raw", ignore_errors=True)
        shutil.copytree(TEMPLATE / "profile", paths.PROFILE, dirs_exist_ok=True)
        shutil.copytree(TEMPLATE / "search", paths.SEARCH, dirs_exist_ok=True)
        self.job, _, _ = jobs.add_advert("Acme AG", "Engineer", "Zürich", "", "C++")
        self.app = web.create_app()
        self.client = self.app.test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.csrf = s["csrf"]

    def upload(self, name, data):
        return self.client.post(f"/job/{self.job}/cv", data={"csrf": self.csrf, "file": (BytesIO(data), name)},
                                content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)

    def test_upload_sets_cv_and_keeps_the_rules_file_as_written(self):
        before = paths.RULES.read_text()
        self.assertIn("CV not found: <code>raw/your-cv.pdf</code>", self.client.get(f"/job/{self.job}").get_data(as_text=True))
        page = self.upload("Ada Example CV.pdf", b"%PDF-1.4 cv")
        self.assertIn("CV saved as raw/Ada Example CV.pdf", page)
        self.assertEqual((paths.DATA / "raw" / "Ada Example CV.pdf").read_bytes(), b"%PDF-1.4 cv")
        self.assertEqual(yaml.safe_load(paths.RULES.read_text())["senders"]["ch"]["cv"], "raw/Ada Example CV.pdf")
        changed = [(a, b) for a, b in zip(before.splitlines(), paths.RULES.read_text().splitlines()) if a != b]
        self.assertEqual(len(changed), 1)  # only the cv: line, its comment kept
        self.assertIn("# path inside the data repo", changed[0][1])
        self.assertIn("Replace CV", self.upload("Ada Example CV.pdf", b"%PDF-1.4 cv"))  # same file again: no copy
        self.assertEqual(sorted(p.name for p in (paths.DATA / "raw").iterdir()), ["Ada Example CV.pdf"])

    def test_a_different_file_with_the_same_name_is_kept_alongside(self):
        self.upload("cv.pdf", b"%PDF old")
        self.upload("cv.pdf", b"%PDF new")
        self.assertEqual((paths.DATA / "raw" / "cv.pdf").read_bytes(), b"%PDF old")
        self.assertEqual(yaml.safe_load(paths.RULES.read_text())["senders"]["ch"]["cv"], "raw/cv-2.pdf")

    def test_cv_line_is_added_when_missing_and_bad_files_are_refused(self):
        paths.RULES.write_text("senders:\n  ch:\n    name: Ada\n    phone: '1'\n  uk:\n    name: Ada\nmax_words: 300\n")
        gmail.set_rules_cv("uk", "raw/uk.pdf")
        rules = yaml.safe_load(paths.RULES.read_text())
        self.assertEqual((rules["senders"]["uk"]["cv"], rules["max_words"]), ("raw/uk.pdf", 300))
        self.assertNotIn("cv", rules["senders"]["ch"])
        with self.assertRaises(ValueError):
            gmail.set_rules_cv("de", "raw/x.pdf")
        self.assertIn("error: choose a CV file", self.upload("notes.txt", b"hello"))

    def test_add_company_from_the_companies_tab(self):
        page = self.client.get("/companies").get_data(as_text=True)
        self.assertIn("Add a company", page)
        r = self.client.post("/sources/add", data={"csrf": self.csrf, "name": "Example Exchange AG", "board": "",
                                                   "careers": "https://example.com/jobs", "history": "", "next": "/companies?"})
        self.assertEqual(r.headers["Location"].rstrip("?"), "/companies")
        self.assertIn("Example Exchange AG", self.client.get("/companies").get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
