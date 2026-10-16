"""Blocklist, posted dates and the Companies page. Run: .venv/bin/python -m unittest discover tests"""
import os
import shutil
import tempfile
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import blocklist, fetch, jobs, paths, web  # noqa: E402  (must follow JOBSEARCH_DATA)

CONFIG = """sources:
  swissdevjobs: true
  companies_file: companies.yaml
filters: {title_include: [engineer], title_exclude: [], locations: [zurich]}
score_terms: {}
"""
COMPANIES = """polled:
  - {name: Acme, board: "greenhouse:acme"}
  - {name: Evil Corp, board: "lever:evil"}
  - {name: Spammy, board: "ashby:spammy"}
"""


def advert(company, url, posted="2026-10-01"):
    return dict(company=company, title="Software Engineer", location="Zurich", url=url, posted=posted, text="C++")


class Blocklist(unittest.TestCase):
    def setUp(self):
        self.assertNotEqual(paths.DATA, paths.REPO / "data", "tests must not touch the real data repo")
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text(CONFIG)
        (paths.SEARCH / "companies.yaml").write_text(COMPANIES)
        for p in (paths.SEARCH / "blocklist.yaml", paths.SEARCH / "seen.txt"):
            p.unlink(missing_ok=True)
        shutil.rmtree(paths.JOBS, ignore_errors=True)

    def test_norm_company(self):
        n = blocklist.norm_company
        self.assertEqual(n("Swissquote Bank SA"), n("swissquote bank"))
        self.assertEqual(n("Ex Machina (Lugano)"), n("Ex Machina"))
        self.assertEqual(n("Zürich Insurance AG"), "zurich insurance")
        self.assertNotEqual(n("Acme"), n("Acme Labs"))

    def test_sources_and_companies_are_separate(self):
        self.assertEqual(blocklist.block_source("swissdevjobs", "low quality"), [])
        self.assertEqual(blocklist.block_company("Evil Corp", ["EvilCo"], "don't like them"), [])
        c = blocklist.Checker()
        self.assertEqual(c.reason({"source": "swissdevjobs:swissdevjobs", "company": "Nice AG"}), "source swissdevjobs")
        self.assertEqual(c.reason({"source": "greenhouse:liked", "company": "Evil Corp Inc."}), "company Evil Corp")
        self.assertEqual(c.reason({"source": "greenhouse:liked", "company": "evilco"}), "company Evil Corp")
        self.assertIsNone(c.reason({"source": "greenhouse:liked", "company": "Nice AG"}))
        blocklist.unblock_source("swissdevjobs")
        self.assertEqual(blocklist.Checker().reason({"source": "swissdevjobs:x", "company": "Evil Corp"}), "company Evil Corp")

    def test_block_validation(self):
        self.assertTrue(blocklist.block_source("greenhouse"))  # would block every greenhouse board
        self.assertTrue(blocklist.block_source("not a source!"))
        self.assertEqual(blocklist.block_source("greenhouse:acme"), [])
        self.assertTrue(blocklist.block_source("greenhouse:acme"))
        self.assertEqual(blocklist.block_company("Acme"), [])
        self.assertTrue(blocklist.block_company("ACME AG"))  # same company

    def test_fetch_skips_blocked_and_records_posted(self):
        blocklist.block_source("ashby:spammy")
        blocklist.block_company("Evil Corp")
        blocklist.block_company("Badco")
        polled = []
        fake = {
            "swissdevjobs": lambda b: polled.append(b) or [advert("Nice AG", "u1"), advert("Badco", "u2")],
            "greenhouse": lambda b: polled.append(b) or [advert("Acme", "u3", "2026-09-30")],
            "lever": lambda b: polled.append(b) or [advert("Evil Corp", "u4")],
            "ashby": lambda b: polled.append(b) or [advert("Spammy", "u5")],
        }
        with mock.patch.dict(fetch.FETCHERS, fake):
            _, new, errors = fetch.run()
        self.assertEqual(errors, [])
        self.assertEqual(polled, ["swissdevjobs", "acme"])  # blocked source and blocked company's board not polled
        self.assertEqual(sorted(j["company"] for j in new), ["Acme", "Nice AG"])
        self.assertNotIn("u2", (paths.SEARCH / "seen.txt").read_text())  # unblocking Badco later lets it through
        acme = next(j for j in jobs.all_jobs() if j["company"] == "Acme")
        self.assertEqual(acme["posted"], "2026-09-30")

    def test_backfill_posted(self):
        job_id = jobs.create_job("Acme", "Software Engineer", "Zurich", "u3", "greenhouse:acme", "text")
        (paths.SEARCH / "seen.txt").write_text("u3\n")
        with mock.patch.dict(fetch.FETCHERS, {"swissdevjobs": lambda b: [], "greenhouse": lambda b: [advert("Acme", "u3", "2026-09-01")],
                                              "lever": lambda b: [], "ashby": lambda b: []}):
            fetch.run()
        self.assertEqual(jobs.load(job_id)["posted"], "2026-09-01")

    def test_day(self):
        self.assertEqual(fetch.day("2026-08-21T10:09:21-04:00"), "2026-08-21")
        self.assertEqual(fetch.day(1786469891368), "2026-08-11")
        self.assertEqual(fetch.day(None), "")

    def test_pages(self):
        jobs.create_job("Evil Corp AG", "Software Engineer", "Zurich", "u9", "greenhouse:liked", "t", extra={"posted": "2026-10-02"})
        jobs.create_job("Nice AG", "Software Engineer", "Zurich", "u8", "greenhouse:liked", "t")
        blocklist.block_company("Evil Corp")
        client = web.create_app().test_client()
        inbox = client.get("/?status=all").get_data(as_text=True)
        self.assertNotIn("Evil Corp AG", inbox)
        self.assertIn("1 hidden (blocked, low salary or outside commute area)", inbox)
        shown = client.get("/?status=all&blocked=1").get_data(as_text=True)
        self.assertIn("Evil Corp AG", shown)
        self.assertIn("2026-10-02", shown)
        self.assertIn(jobs.today(), shown)  # Found date
        companies = client.get("/companies").get_data(as_text=True)
        self.assertIn("Nice AG", companies)
        self.assertIn("Blocked companies (1)", companies)
        self.assertIn("Blocked sources (0)", client.get("/sources").get_data(as_text=True))


class InboxBlockButton(unittest.TestCase):
    def setUp(self):
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text(CONFIG)
        (paths.SEARCH / "blocklist.yaml").unlink(missing_ok=True)
        shutil.rmtree(paths.JOBS, ignore_errors=True)

    def test_block_from_inbox_row(self):
        jobs.create_job("Pushy Recruiting Ltd", "Software Engineer", "Zurich", "u1", "swissdevjobs:x", "t")
        jobs.create_job("pushy recruiting", "Developer", "Zurich", "u2", "greenhouse:y", "t")
        client = web.create_app().test_client()
        page = client.get("/").get_data(as_text=True)
        self.assertIn(">Block</button>", page)
        with client.session_transaction() as s:
            csrf = s["csrf"]
        r = client.post("/companies/block", data={"csrf": csrf, "name": "Pushy Recruiting Ltd", "reason": "spam", "next": "/"},
                        follow_redirects=True).get_data(as_text=True)
        self.assertIn("2 adverts hidden", r)  # same company from both sources
        self.assertNotIn("Pushy", client.get("/").get_data(as_text=True))


class MinSalary(unittest.TestCase):
    def setUp(self):
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text(CONFIG + "min_score: 0\nmin_salary: 150000  # keep me\n")
        (paths.SEARCH / "companies.yaml").write_text("polled: []\n")
        (paths.SEARCH / "blocklist.yaml").unlink(missing_ok=True)
        (paths.SEARCH / "seen.txt").unlink(missing_ok=True)
        shutil.rmtree(paths.JOBS, ignore_errors=True)

    def test_parsing(self):
        self.assertEqual(jobs.salary_max({"salary": "CHF 100,000-120,000"}), 120000)
        self.assertIsNone(jobs.salary_max({"salary": "CHF 140,000-0"}))  # no upper bound stated
        self.assertIsNone(jobs.below_salary({"salary": "CHF 140,000-155,000"}))
        self.assertIn("120,000", jobs.below_salary({"salary": "CHF 100,000-120,000"}))
        self.assertIsNone(jobs.below_salary({}))

    def test_fetch_skips_low_salary_and_keeps_unstated(self):
        low = dict(advert("Low AG", "s1"), extra={"salary": "CHF 90,000-110,000"})
        fair = dict(advert("Fair AG", "s2"), extra={"salary": "CHF 140,000-160,000"})
        none = dict(advert("Quiet AG", "s3"), extra={})
        with mock.patch.dict(fetch.FETCHERS, {"swissdevjobs": lambda b: [low, fair, none]}):
            _, new, _ = fetch.run()
        self.assertEqual(sorted(j["company"] for j in new), ["Fair AG", "Quiet AG"])

    def test_inbox_hides_only_untouched_low_salary(self):
        jobs.create_job("Low AG", "Engineer", "Zurich", "u1", "swissdevjobs:x", "t", extra={"salary": "CHF 90,000-110,000"})
        b = jobs.create_job("Kept AG", "Engineer", "Zurich", "u2", "swissdevjobs:x", "t", extra={"salary": "CHF 90,000-110,000"})
        jobs.set_status(b, "shortlisted")
        page = web.create_app().test_client().get("/?status=all").get_data(as_text=True)
        self.assertNotIn("Low AG", page)
        self.assertIn("Kept AG", page)
        self.assertIn("1 hidden (blocked, low salary or outside commute area)", page)

    def test_salary_from_advert_text_in_other_currencies(self):
        (paths.SEARCH / "config.yaml").write_text(CONFIG + "min_salary: 150000\nfx_to_chf: {EUR: 0.93, GBP: 1.07}\n")
        x = jobs.extract_salary
        self.assertEqual(x("Paris - Base Annual Gross - 50,000€ to 77,000€ - Final"), "EUR 50,000-77,000")
        self.assertEqual(x("Paris: 53,000 - 82,000 EUR gross"), "EUR 53,000-82,000")
        self.assertEqual(x("CHF 9'000 - 11'000 per month"), "CHF 108,000-132,000")
        self.assertIsNone(x("serving 350,000 - 400,000 customers"))  # no currency
        self.assertIsNone(x("CHF 80 - 120 per hour"))
        self.assertEqual(x("Paris: 50,000€ to 77,000€. Zurich: CHF 140'000 - 180'000"), "CHF 140,000-180,000")  # highest wins
        self.assertEqual(jobs.salary_max({"salary": "EUR 50,000-77,000"}), 71610)
        self.assertIn("stated EUR 50,000-77,000", jobs.below_salary({"salary": "EUR 50,000-77,000"}))
        self.assertIsNone(jobs.salary_max({"salary": "JPY 5,000,000-8,000,000"}))  # unknown currency: never hidden
        # a foreign-currency range doesn't hide a role that is also offered in Switzerland
        self.assertIsNone(jobs.below_salary({"salary": "EUR 50,000-77,000", "location": "Geneva, Switzerland, Paris, France"}))
        self.assertIsNotNone(jobs.below_salary({"salary": "EUR 50,000-77,000", "location": "Paris"}))
        self.assertIsNotNone(jobs.below_salary({"salary": "CHF 90,000-110,000", "location": "Geneva"}))

    def test_fetch_reads_salary_from_text(self):
        (paths.SEARCH / "config.yaml").write_text(CONFIG + "min_score: 0\nmin_salary: 150000\nfx_to_chf: {EUR: 0.93}\n")
        low = dict(advert("Low AG", "t1"), text="Engineer. Salary CHF 90'000 - 110'000.")
        with mock.patch.dict(fetch.FETCHERS, {"swissdevjobs": lambda b: [low]}):
            _, new, _ = fetch.run()
        self.assertEqual(new, [])

    def test_commute_area(self):
        area = [r"z[uü]rich|winterthur", r"\bzug\b|baar", r"lugano|ticino"]
        out = jobs.outside_commute
        self.assertIsNone(out({"location": "Zug, Switzerland"}, area))
        self.assertIsNone(out({"location": "Geneva; Zurich"}, area))  # one commutable office is enough
        self.assertIsNone(out({"location": "Switzerland"}, area))  # no city to judge
        self.assertIsNone(out({"location": "London, UK Remote"}, area))
        self.assertIn("Geneva", out({"location": "Geneva, Switzerland, Paris, France"}, area))
        self.assertIsNotNone(out({"location": "Bern (hybrid)"}, area))
        self.assertIsNone(out({"location": "Geneva"}, []))  # no commute_area configured: off

    def test_fetch_and_inbox_apply_commute_area(self):
        (paths.SEARCH / "config.yaml").write_text(CONFIG.replace("locations: [zurich]", "locations: [zurich, geneva, switzerland]")
                                                  + "min_score: 0\ncommute_area: ['z[uü]rich']\n")
        far = dict(advert("Far SA", "c1"), location="Geneva, Switzerland")
        near = dict(advert("Near AG", "c2"), location="Zurich")
        with mock.patch.dict(fetch.FETCHERS, {"swissdevjobs": lambda b: [far, near]}):
            _, new, _ = fetch.run()
        self.assertEqual([j["company"] for j in new], ["Near AG"])
        jobs.create_job("Old Geneva SA", "Engineer", "Geneva", "c3", "manual", "t")
        page = web.create_app().test_client().get("/?status=all&blocked=1").get_data(as_text=True)
        self.assertIn("office outside commute area (Geneva)", page)

    def test_set_min_salary_keeps_comments(self):
        from jobsearch import sources
        sources.set_min_salary(120000)
        text = (paths.SEARCH / "config.yaml").read_text()
        self.assertIn("min_salary: 120000", text)
        self.assertEqual(jobs.min_salary(), 120000)


if __name__ == "__main__":
    unittest.main()
