"""Workday fetcher, with Workday's JSON API mocked. Run: .venv/bin/python -m unittest discover tests"""
import datetime as dt
import os
import tempfile
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import fetch, sources  # noqa: E402  (must follow JOBSEARCH_DATA)

COUNTRY_FACETS = [{"facetParameter": "Location_Country", "values": [
    {"descriptor": "Singapore", "id": "sg"}, {"descriptor": "Switzerland", "id": "ch"}]}]
CITY_FACETS = [{"facetParameter": "locationMainGroup", "values": [{"facetParameter": "locations", "values": [
    {"descriptor": "Pleasanton", "id": "us1"}, {"descriptor": "Rotkreuz", "id": "rk"}, {"descriptor": "Basel", "id": "bs"}]}]}]


def postings(n, start=0):
    return [{"title": f"Software Engineer {i}", "externalPath": f"/job/Zurich/SE_{i}", "locationsText": "Zurich",
             "postedOn": "Posted 3 Days Ago"} for i in range(start, start + n)]


class Workday(unittest.TestCase):
    def test_swiss_facets(self):
        self.assertEqual(fetch._workday_swiss_facets(COUNTRY_FACETS), {"Location_Country": ["ch"]})
        self.assertEqual(fetch._workday_swiss_facets(CITY_FACETS), {"locations": ["rk", "bs"]})  # no country facet
        self.assertEqual(fetch._workday_swiss_facets([]), {})

    def test_posted(self):
        today = dt.date.today()
        self.assertEqual(fetch._workday_posted("Posted Today"), today.isoformat())
        self.assertEqual(fetch._workday_posted("Posted 30+ Days Ago"), (today - dt.timedelta(days=30)).isoformat())
        self.assertEqual(fetch._workday_posted("whenever"), "")

    def test_pages_through_swiss_postings(self):
        calls = []

        def fake_post(url, payload):
            calls.append(payload)
            if payload["appliedFacets"] == {}:
                return {"total": 999, "facets": COUNTRY_FACETS, "jobPostings": postings(20)}
            first = payload["offset"] == 0
            return {"total": 25 if first else 0, "jobPostings": postings(20 if first else 5, payload["offset"])}
        with mock.patch.object(fetch, "post", fake_post):
            ads = list(fetch.workday("acme.wd3/Careers"))
        self.assertEqual(len(ads), 25)
        self.assertEqual(calls[1]["appliedFacets"], {"Location_Country": ["ch"]})
        self.assertEqual(ads[0]["url"], "https://acme.wd3.myworkdayjobs.com/Careers/job/Zurich/SE_0")
        self.assertEqual(ads[0]["ref"], "https://acme.wd3.myworkdayjobs.com/wday/cxs/acme/Careers/job/Zurich/SE_0")
        self.assertEqual(ads[0]["location"], "Zurich, Switzerland")
        self.assertIsNone(ads[0]["text"])  # fetched later, only for adverts that pass the filters

    def test_detail(self):
        resp = mock.Mock()
        resp.json.return_value = {"jobPostingInfo": {"jobDescription": "<p>Write <b>C++</b></p>", "location": "Rotkreuz",
                                                     "additionalLocations": ["Basel"], "startDate": "2026-10-01"}}
        with mock.patch.object(fetch, "get", return_value=resp):
            d = fetch.workday_detail("https://x")
        self.assertEqual((d["location"], d["posted"]), ("Rotkreuz; Basel", "2026-10-01"))
        self.assertIn("C++", d["text"])

    def test_pasted_careers_url_becomes_a_board(self):
        self.assertEqual(sources.normalize_board("https://juliusbaer.wd3.myworkdayjobs.com/en-US/External/job/x"),
                         "workday:juliusbaer.wd3/External")
        self.assertEqual(sources.normalize_board("https://swisscom.wd103.myworkdayjobs.com/SwisscomExternalCareers"),
                         "workday:swisscom.wd103/SwisscomExternalCareers")
        self.assertEqual(sources.normalize_board("greenhouse:acme"), "greenhouse:acme")


if __name__ == "__main__":
    unittest.main()
