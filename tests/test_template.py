"""The starter data template in examples/ must work as a data repo."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class Template(unittest.TestCase):
    def test_template_checks_a_letter(self):
        data = Path(tempfile.mkdtemp()) / "data"
        shutil.copytree(REPO / "examples" / "data-template", data)
        job = data / "jobs" / "2026-01-01-acme-engineer"
        job.mkdir()
        (job / "posting.md").write_text("---\ncompany: Acme\ntitle: Engineer\nstatus: drafting\n---\nC++ engineer wanted.\n")
        (job / "letter.draft.md").write_text("---\nto: Acme\n---\nDear Hiring Team,\n\n"
                                             "I maintained the C++ pricing service at Example AG from 2020 to 2025. [F:EXAMPLE-ROLE]\n\n"
                                             "I would welcome the opportunity to discuss the role with you.\n\nKind regards,\n\nYour Name\n")
        env = {**os.environ, "JOBSEARCH_DATA": str(data)}
        out = subprocess.run([sys.executable, "-m", "jobsearch.cli", "check", job.name], cwd=REPO, env=env,
                             capture_output=True, text=True)
        self.assertIn("PASS", out.stdout, out.stdout + out.stderr)
        out = subprocess.run([sys.executable, "-m", "jobsearch.cli", "list"], cwd=REPO, env=env, capture_output=True, text=True)
        self.assertEqual(out.returncode, 0, out.stderr)


if __name__ == "__main__":
    unittest.main()
