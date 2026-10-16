"""Buttons must work without browser pop-ups: browsers let users silently block prompt()/confirm()
for a site ("don't allow this page to show more dialogs"), which made buttons do nothing."""
import re
import unittest
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent.parent / "jobsearch" / "templates"


class NoPopups(unittest.TestCase):
    def test_templates_use_no_dialogs(self):
        offenders = [f"{p.name}:{i}" for p in TEMPLATES.glob("*.html")
                     for i, line in enumerate(p.read_text().splitlines(), 1)
                     if re.search(r"\b(prompt|confirm|alert)\s*\(", line)]
        self.assertEqual(offenders, [], "use an inline field or a two-step <details class=confirm> instead")


if __name__ == "__main__":
    unittest.main()
