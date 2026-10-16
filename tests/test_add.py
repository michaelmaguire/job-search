"""Adding adverts found elsewhere (web form / add-job). Run: .venv/bin/python -m unittest discover tests"""
import os
import shutil
import tempfile
import unittest

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import jobs, paths, web  # noqa: E402  (must follow JOBSEARCH_DATA)

CONFIG = """sources: {companies_file: companies.yaml}
filters: {title_include: [engineer], title_exclude: [], locations: [zurich]}
score_terms: {"c\\\\+\\\\+": 3}
min_salary: 150000
commute_area: ['z[uü]rich|zug']
"""
ADVERT = "We build trading systems in C++. Salary CHF 160'000 - 190'000."


class AddAdvert(unittest.TestCase):
    def setUp(self):
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text(CONFIG)
        (paths.SEARCH / "companies.yaml").write_text("polled: []\n")
        (paths.SEARCH / "blocklist.yaml").unlink(missing_ok=True)
        shutil.rmtree(paths.JOBS, ignore_errors=True)
        self.client = web.create_app().test_client()
        self.client.get("/")
        with self.client.session_transaction() as s:
            self.csrf = s["csrf"]

    def post(self, **form):
        return self.client.post("/add", data={"csrf": self.csrf, "status": "shortlisted", **form}, follow_redirects=True)

    def test_adds_linkedin_advert_with_clean_url_salary_and_score(self):
        page = self.post(company="Acme AG", title="C++ Engineer", location="Zug",
                         url="https://ch.linkedin.com/jobs/view/c-engineer-at-acme-4012345678?trk=abc", text=ADVERT)
        self.assertIn("added", page.get_data(as_text=True))
        j = jobs.all_jobs()[0]
        self.assertEqual((j["url"], j["source"], j["status"], j["salary"]),
                         ("https://www.linkedin.com/jobs/view/4012345678/", "linkedin", "shortlisted", "CHF 160,000-190,000"))
        self.assertEqual(j["score"], 3)
        self.assertIn("C++", jobs.load(j["id"])["body"])

    def test_no_duplicates_and_summary_gets_full_text(self):
        jid = jobs.create_job("Acme AG", "C++ Engineer", "Zug", "https://www.linkedin.com/jobs/view/4012345678/",
                              "swissdevjobs:x", "summary", extra={"summary_only": True})
        page = self.post(company="ACME", title="c++ engineer", url="https://www.linkedin.com/jobs/view/4012345678/?refId=1",
                         text=ADVERT).get_data(as_text=True)
        self.assertIn("replaced the summary", page)
        self.assertEqual(len(jobs.all_jobs()), 1)
        j = jobs.load(jid)
        self.assertNotIn("summary_only", j)
        self.assertIn("trading systems", j["body"])
        page = self.post(company="Acme AG", title="C++ Engineer", text="again").get_data(as_text=True)
        self.assertIn("already saved", page)
        self.assertEqual(len(jobs.all_jobs()), 1)

    def test_warns_outside_commute_area_but_keeps_it(self):
        page = self.post(company="Far SA", title="Engineer", location="Geneva", text="Salary CHF 90'000 - 110'000").get_data(as_text=True)
        self.assertIn("outside commute area", page)
        self.assertIn("below", page.replace("&lt;", "below"))  # salary warning too
        self.assertEqual(jobs.all_jobs()[0]["status"], "shortlisted")

    def test_company_and_title_required(self):
        self.assertIn("company and title are needed", self.post(company="", title="").get_data(as_text=True))
        self.assertEqual(jobs.all_jobs(), [])


if __name__ == "__main__":
    unittest.main()
