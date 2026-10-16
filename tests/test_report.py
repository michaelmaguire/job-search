"""Official form 716.007 i filling. Run: .venv/bin/python -m unittest discover tests"""
import os
import tempfile
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from pypdf import PdfReader  # noqa: E402

from jobsearch import efforts, paths, report  # noqa: E402  (must follow JOBSEARCH_DATA)


def app(day, company, **kw):
    row = dict.fromkeys(efforts.FIELDS, "")
    row.update(date=f"2030-01-{day:02d}", type="application", company=company, job_title="Engineer",
               method="electronic", workload="100%", **kw)
    return row


class OfficialForm(unittest.TestCase):
    def setUp(self):
        rows = [r for r in efforts.read() if not r["date"].startswith("2030-01")]
        rows += [app(d, f"Firm {d}") for d in range(1, 16)]  # 15 -> two copies
        rows[-1].update(workload="80%", method="phone")
        rows.append(dict(app(2, "Firm 2"), type="result", result="rejected", rejection_reason="German C2 needed"))
        efforts.write(rows)

    def test_fills_two_copies_with_independent_fields(self):
        out, count = report.official_pdf("2030-01", out=paths.REPORTS / "t.pdf", name="TEST, NAME", ahv="756.0000.0000.00")
        self.assertEqual(count, 15)
        f = PdfReader(out).get_fields()
        self.assertEqual(len(PdfReader(out).pages), 4)
        self.assertEqual(f["Cognome e nome"]["/V"], "TEST, NAME")
        self.assertEqual(f["Mese e anno"]["/V"], "gennaio 2030")
        self.assertEqual(f["giorno-mese 1"]["/V"], "01.01")
        self.assertEqual(f["non assunzione_2"]["/V"], "/Ja")
        self.assertEqual(f["Motivo_2"]["/V"], "German C2 needed")
        self.assertEqual(f["in sospeso_1"]["/V"], "/Ja")
        self.assertEqual(f["giorno-mese 1 #2"]["/V"], "15.01")  # second copy, own field
        self.assertEqual(f["telefonica_1 #2"]["/V"], "/Ja")
        self.assertEqual(f["a tempo parz (%)_1 #2"]["/V"], "/Ja")
        self.assertIn("(80%)", f["Impiego in qualità di ?_1 #2"]["/V"])

    def test_insured_details_come_from_config(self):
        cfg = tempfile.mkdtemp()
        with open(os.path.join(cfg, "insured.yaml"), "w") as fh:
            fh.write("name: CONFIG, PERSON\nahv: 756.1111.2222.33\n")
        with mock.patch.object(report, "INSURED", report.Path(cfg) / "insured.yaml"):
            out, _ = report.official_pdf("2030-01", out=paths.REPORTS / "t2.pdf")
        f = PdfReader(out).get_fields()
        self.assertEqual((f["Cognome e nome"]["/V"], f["No AVS"]["/V"]), ("CONFIG, PERSON", "756.1111.2222.33"))


if __name__ == "__main__":
    unittest.main()
