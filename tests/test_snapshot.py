"""Advert PDF snapshots, with a fake Chrome. Run: .venv/bin/python -m unittest discover tests"""
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import jobs, snapshot  # noqa: E402  (must follow JOBSEARCH_DATA)

FAKE_CHROME = """#!/bin/sh
[ "$1" = --version ] && echo "Google Chrome 152.0.7977.64" && exit 0
echo "$@" > "$0.args"
for a in "$@"; do case "$a" in --print-to-pdf=*) out="${a#--print-to-pdf=}";; esac; done
[ -n "$out" ] && head -c 5000 /dev/zero > "$out"
"""
# A one-page PDF of Chrome's error screen, as printed when a site refuses headless Chrome.
def _pdf(text):
    content = b"BT /F1 12 Tf 72 720 Td (" + text + b") Tj ET"
    objs = [b"<</Type/Catalog/Pages 2 0 R>>", b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
            b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
            b"<</Length %d>>stream\n%s\nendstream" % (len(content), content),
            b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>"]
    out, offsets = b"%PDF-1.4\n%" + b" " * 1200 + b"\n", []  # padded past the 1000-byte "empty" check
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj%s endobj\n" % (i, body)
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1) + b"".join(b"%010d 00000 n \n" % o for o in offsets)
    return out + b"trailer<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


ERROR_PDF = _pdf(b"This site can't be reached ERR_HTTP2_PROTOCOL_ERROR")


class Snapshot(unittest.TestCase):
    def setUp(self):
        self.bin = Path(tempfile.mkdtemp()) / "chrome"
        self.bin.write_text(FAKE_CHROME)
        self.bin.chmod(self.bin.stat().st_mode | stat.S_IEXEC)
        self.job = jobs.create_job("Snap AG", "Engineer", "Zurich", "https://example.com/job", "manual", "text")

    def test_take_names_by_date_and_never_overwrites(self):
        with mock.patch.dict(os.environ, {"JOBSEARCH_CHROME": str(self.bin)}):
            first, second = snapshot.take(self.job), snapshot.take(self.job)
        self.assertEqual(first.name, f"advert-{jobs.today()}.pdf")
        self.assertEqual(second.name, f"advert-{jobs.today()}-2.pdf")

    def test_auto_on_shortlist_only_once(self):
        with mock.patch.dict(os.environ, {"JOBSEARCH_CHROME": str(self.bin), "JOBSEARCH_SNAPSHOT": "on"}):
            jobs.set_status(self.job, "skipped")
            self._wait()
            self.assertEqual(snapshot.existing(self.job), [])
            jobs.set_status(self.job, "shortlisted")
            self._wait()
            jobs.set_status(self.job, "applied")
            self._wait()
        self.assertEqual(len(snapshot.existing(self.job)), 1)

    def test_presents_as_regular_chrome(self):
        with mock.patch.dict(os.environ, {"JOBSEARCH_CHROME": str(self.bin)}):
            snapshot.take(self.job)
        args = Path(f"{self.bin}.args").read_text()
        self.assertIn("--user-agent=Mozilla/5.0", args)
        self.assertIn("Chrome/152.0.0.0", args)
        self.assertNotIn("Headless", args)

    def test_chrome_error_page_is_not_a_snapshot(self):
        pdf = Path(tempfile.mkdtemp()) / "error.pdf"
        pdf.write_bytes(ERROR_PDF)
        self.bin.write_text(FAKE_CHROME.replace('head -c 5000 /dev/zero > "$out"', f'cp {pdf} "$out"'))
        with mock.patch.dict(os.environ, {"JOBSEARCH_CHROME": str(self.bin)}):
            with self.assertRaises(RuntimeError) as e:
                snapshot.take(self.job)
        self.assertIn("ERR_HTTP2_PROTOCOL_ERROR", str(e.exception))
        self.assertEqual(snapshot.existing(self.job), [])

    def test_failure_is_recorded(self):
        with mock.patch.dict(os.environ, {"JOBSEARCH_CHROME": "/bin/true", "JOBSEARCH_SNAPSHOT": "on"}):
            jobs.set_status(self.job, "drafting")
            self._wait()
        self.assertIn("no PDF", (jobs.job_dir(self.job) / "advert-snapshot.error.txt").read_text())

    def _wait(self):
        for t in threading.enumerate():
            if t is not threading.current_thread() and not t.daemon:
                t.join(10)


if __name__ == "__main__":
    unittest.main()
