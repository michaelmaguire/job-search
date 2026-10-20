"""Sources tab tests (companies.yaml editing). Run: .venv/bin/python -m unittest discover tests"""
import os
import tempfile
import unittest

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import paths, sources, web  # noqa: E402  (must follow JOBSEARCH_DATA)

CONFIG = """sources:
  swissdevjobs: true          # comment kept
  companies_file: companies.yaml
filters: {title_include: [engineer], title_exclude: [], locations: [zurich]}
score_terms: {}
"""
COMPANIES = """# header comment
polled:
  - {name: Acme, board: "greenhouse:acme", history: "applied 2025"}

manual:
  # Finance
  - {name: Bank, careers: "https://bank.example/jobs"}
  # Other
  - {name: "IT'IS Foundation"}
"""


class Sources(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(paths.DATA, paths.REPO / "data", "tests must not touch the real data repo")
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text(CONFIG)
        (paths.SEARCH / "companies.yaml").write_text(COMPANIES)

    def text(self):
        return (paths.SEARCH / "companies.yaml").read_text()

    def test_entries_and_groups(self):
        e = sources.entries()
        self.assertEqual([x["name"] for x in e["polled"]], ["Acme"])
        self.assertEqual([(x["name"], x["group"]) for x in e["manual"]], [("Bank", "Finance"), ("IT'IS Foundation", "Other")])

    def test_add_keeps_comments_and_picks_section(self):
        self.assertEqual(sources.add({"name": "Beta", "board": "lever:beta"}), [])
        self.assertEqual(sources.add({"name": "Gamma"}), [])
        t = self.text()
        self.assertIn("# header comment", t)
        self.assertIn("# Finance", t)
        self.assertEqual([x["name"] for x in sources.entries()["polled"]], ["Acme", "Beta"])
        self.assertEqual(sources.entries()["manual"][-1]["name"], "Gamma")

    def test_validation(self):
        self.assertTrue(sources.add({"name": "X", "board": "workday:x"}))
        self.assertTrue(sources.add({"name": "Dup", "board": "greenhouse:acme"}))
        self.assertTrue(sources.add({"name": "Y", "careers": "not a url"}))
        self.assertTrue(sources.add({"name": ""}))

    def test_update_moves_between_sections(self):
        bank = sources.entries()["manual"][0]
        self.assertEqual(sources.update(bank["line"], "Bank", {**bank, "board": "ashby:bank"}), [])
        e = sources.entries()
        self.assertEqual([x["name"] for x in e["polled"]], ["Acme", "Bank"])
        self.assertEqual(e["polled"][1]["careers"], "https://bank.example/jobs")
        acme = e["polled"][0]
        self.assertEqual(sources.update(acme["line"], "Acme", {**acme, "history": "new"}), [])
        self.assertEqual(sources.entries()["polled"][0]["history"], "new")

    def test_stale_line_is_refused(self):
        with self.assertRaises(ValueError):
            sources.remove(sources.entries()["polled"][0]["line"], "Not Acme")
        itis = sources.entries()["manual"][1]
        sources.remove(itis["line"], "IT'IS Foundation")
        self.assertNotIn("IT'IS", self.text())

    def test_job_site_switch(self):
        sources.set_site("swissdevjobs", False)
        self.assertFalse(sources.site_enabled("swissdevjobs"))
        self.assertIn("# comment kept", (paths.SEARCH / "config.yaml").read_text())
        sources.set_site("swissdevjobs", True)
        self.assertTrue(sources.site_enabled("swissdevjobs"))
        with self.assertRaises(ValueError):
            sources.set_site("nosuchsite", True)
        site = sources.job_sites({"swissdevjobs:swissdevjobs": 4})[0]
        self.assertEqual((site["name"], site["on"], site["count"]), ("swissdevjobs.ch", True, 4))

    def test_page_renders(self):
        app = web.create_app()
        client = app.test_client()
        page = client.get("/sources").get_data(as_text=True)
        self.assertIn("greenhouse:acme", page)
        self.assertIn("Finance", page)
        self.assertIn("Job sites (2)", page)
        self.assertIn("Company boards - polled automatically", page)
        self.assertIn("Yes, remove IT&#39;IS Foundation", page)  # two-step remove, no pop-up


if __name__ == "__main__":
    unittest.main()
