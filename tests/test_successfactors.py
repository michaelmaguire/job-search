"""SuccessFactors Career Site Builder fetcher, with the site mocked."""
import os
import tempfile
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import fetch, sources  # noqa: E402  (must follow JOBSEARCH_DATA)


def page(rows, total):
    trs = "".join(f'<tr class="data-row"><td><a class="jobTitle-link" href="/job/x-{i}/{i}/">Software Engineer {i}</a></td>'
                  f'<td><span class="jobLocation"> {loc} </span></td><td><span class="jobDate">15 Oct 2026</span></td></tr>'
                  for i, loc in rows)
    return f'<span class="paginationLabel">Results 1 – 25 of {total}</span><table>{trs}</table>'


class SuccessFactors(unittest.TestCase):
    def test_pages_and_marks_swiss_rows(self):
        pages = {0: page([(i, "Zurich, Zurich, CH" if i % 2 else "Madrid, ES") for i in range(25)], 30),
                 25: page([(i, "Zurich, CH") for i in range(25, 30)], 30)}

        def fake_get(url, **kw):
            r = mock.Mock()
            r.text = pages[kw["params"]["startrow"]]
            return r
        with mock.patch.object(fetch, "get", fake_get):
            ads = list(fetch.successfactors("careers.example.com"))
        self.assertEqual(len(ads), 30)
        self.assertEqual(ads[1]["location"], "Zurich, Zurich, CH (Switzerland)")
        self.assertEqual(ads[0]["location"], "Madrid, ES")
        self.assertEqual(ads[1]["url"], "https://careers.example.com/job/x-1/1/")
        self.assertEqual(ads[1]["posted"], "2026-10-15")

    def test_detail(self):
        r = mock.Mock()
        r.text = ('<meta itemprop="datePosted" content="Sat Sep 26 02:00:00 UTC 2026">'
                  '<span class="jobdescription"><p>We need <b>C++</b> and Linux.</p></span>')
        with mock.patch.object(fetch, "get", return_value=r):
            d = fetch.successfactors_detail("https://careers.example.com/job/x/1/")
        self.assertEqual(d["posted"], "2026-09-26")
        self.assertIn("C++", d["text"])

    def test_board_format(self):
        self.assertEqual(sources.normalize_board("successfactors:https://careers.swissre.com/search/"), "successfactors:careers.swissre.com")
        self.assertTrue(sources.validate({"name": "X", "board": "successfactors:nohost"}))


if __name__ == "__main__":
    unittest.main()
