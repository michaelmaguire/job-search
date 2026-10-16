"""The browser version's server side (web/bridge.py: the web app with its browser swaps), run here in CPython."""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials
sys.path.insert(0, str(REPO / "web"))

import bridge  # noqa: E402  (must follow JOBSEARCH_DATA)

from jobsearch import jobs, paths  # noqa: E402

try:
    import fpdf  # noqa: F401  (pip install -e ".[dev]")
    HAVE_FPDF = True
except ImportError:
    HAVE_FPDF = False


def setUpModule():
    global undo
    undo = bridge.start()


def tearDownModule():
    undo()  # the other test modules get the local app's jobsearch back


def call(method, path, fields=()):
    r = json.loads(bridge.handle(method, path, json.dumps([list(f) for f in fields])))
    return r, bridge.LAST_BODY


class Bridge(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(paths.JOBS, ignore_errors=True)
        shutil.copytree(REPO / "examples" / "data-template" / "profile", paths.PROFILE, dirs_exist_ok=True)
        shutil.copytree(REPO / "examples" / "data-template" / "search", paths.SEARCH, dirs_exist_ok=True)
        call("GET", "/")
        self.csrf = json.loads(bridge.csrf())

    def post(self, path, **fields):
        return call("POST", path, [("csrf", self.csrf), *fields.items()])

    def test_pages_render_with_browser_navigation(self):
        bridge.set_repo(json.dumps({"changes": ["?? jobs/x/notes.md"], "branch": "main"}))
        r, _ = call("GET", "/")
        self.assertEqual((r["status"], r["path"]), (200, "/"))
        self.assertIn('href="/browser/data"', r["text"])
        self.assertIn("Commit data", r["text"])
        self.assertNotIn("Commit &amp; push", r["text"])
        for page in ("/efforts", "/sources", "/companies", "/recruiters", "/report", "/facts", "/browser/data"):
            self.assertEqual(call("GET", page)[0]["status"], 200, page)

    def test_add_advert_follows_the_redirect_and_flashes(self):
        r, _ = self.post("/add", company="Acme AG", title="C++ Engineer", location="Zürich", text="C++ on Linux",
                         status="shortlisted", url="https://acme.example/jobs/1")
        job_id = jobs.all_jobs()[0]["id"]
        self.assertEqual(r["path"], f"/job/{job_id}")
        self.assertIn("added", r["text"])
        self.assertIn("Drafting with Claude needs the local app", r["text"])
        self.assertIn("Write in Gmail", r["text"])
        self.assertNotIn("Save PDF of advert page</button>", r["text"])
        r, _ = self.post(f"/job/{job_id}/emails/none.md", action="remove")
        self.assertEqual(r["fragment"], "emails")

    def test_files_come_back_as_bytes(self):
        self.post("/add", company="Acme AG", title="Engineer", text="C++")
        job_id = jobs.all_jobs()[0]["id"]
        r, body = call("GET", f"/job/{job_id}/file/posting.md")
        self.assertNotIn("text", r)
        self.assertEqual(r["filename"], "posting.md")
        self.assertIn(b"C++", body)

    def test_uploaded_files_reach_flask(self):
        self.post("/add", company="Acme AG", title="Engineer", text="C++")
        job_id = jobs.all_jobs()[0]["id"]
        r = json.loads(bridge.handle("POST", f"/job/{job_id}/cv", json.dumps([["csrf", self.csrf]]),
                                     [("file", "My CV.pdf", b"%PDF-1.4 cv")]))
        self.assertIn("CV saved as raw/My CV.pdf", r["text"])
        self.assertEqual((paths.DATA / "raw" / "My CV.pdf").read_bytes(), b"%PDF-1.4 cv")

    def test_messages_from_worker_actions_show_on_the_next_page(self):
        bridge.flash_later("committed 2 file(s): test")
        self.assertIn("committed 2 file(s): test", call("GET", "/")[0]["text"])

    def test_settings_and_file_editor(self):
        self.post("/browser/author", name="Ada Example", email="ada@example.com")
        self.assertEqual(bridge.author()["email"], "ada@example.com")
        self.post("/browser/insured", name="Ada Example", ahv="756.0000.0000.00")
        self.assertIn("756.0000.0000.00", call("GET", "/browser/data")[0]["text"])
        r, _ = self.post("/browser/edit?path=search/config.yaml", text="a: [unclosed\n")
        self.assertIn("isn&#39;t valid YAML", r["text"])
        self.post("/browser/edit?path=search/config.yaml", text="titles: []\n")
        self.assertEqual((paths.SEARCH / "config.yaml").read_text(), "titles: []\n")
        self.assertEqual(call("GET", "/browser/edit?path=../outside.yaml")[0]["status"], 404)

    @unittest.skipUnless(HAVE_FPDF, "fpdf2 not installed")
    def test_render_makes_pdf_and_docx_without_libreoffice(self):
        self.post("/add", company="Acme AG", title="Engineer", text="C++ on Linux")
        job_id = jobs.all_jobs()[0]["id"]
        draft = ("---\nto: |\n  Hiring Team\n  Acme AG\nsubject: Application for Engineer\n---\nDear Hiring Team,\n\n"
                 "The role uses **C++** on Linux. [J]\n\n- C++ on Linux [J]\n- Linux – everyday [J]\n\nKind regards,\n\nYour Name\n")
        (jobs.job_dir(job_id) / "letter.draft.md").write_text(draft)
        r, _ = self.post(f"/job/{job_id}/render", force="1")
        out = sorted(p.name for p in jobs.job_dir(job_id).iterdir() if "-CoverLetter-" in p.name)
        self.assertEqual([n.rsplit(".", 1)[1] for n in out], ["docx", "pdf"])
        pdf = (jobs.job_dir(job_id) / out[1]).read_bytes()
        self.assertTrue(pdf.startswith(b"%PDF"))
        with zipfile.ZipFile(jobs.job_dir(job_id) / out[0]) as z:
            doc = z.read("word/document.xml").decode()
        self.assertIn("Application for Engineer", doc)
        self.assertIn("Linux – everyday", doc)
        self.assertIn("<w:b/>", doc)

    def test_the_browser_loads_every_module_and_template(self):
        js = (REPO / "web" / "runtime.js").read_text()
        listed = set(re.findall(r'"(\w+)"', js[js.index("PY_FILES"):js.index("export const FORMS")]))
        wanted = {p.stem for p in (REPO / "jobsearch").glob("*.py") if p.stem != "cli"}
        wanted |= {p.stem for p in (REPO / "jobsearch" / "templates").glob("*.html")}
        self.assertEqual(wanted - listed, set())
        for f in re.findall(r'"(forms/[^"]+)"', js):
            self.assertTrue((REPO / "jobsearch" / f).exists(), f)


if __name__ == "__main__":
    unittest.main()
