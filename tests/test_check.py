"""Checker tests. Run: .venv/bin/python -m unittest discover tests"""
import os
import shutil
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials
TMP = Path(os.environ["JOBSEARCH_DATA"])
shutil.copytree(REPO / "examples" / "data-template" / "profile", TMP / "profile", dirs_exist_ok=True)
with open(TMP / "profile" / "facts.yaml", "a") as f:  # fictional facts the tests rely on; never the real data repo
    f.write("""
  - id: TEST-MIGRATE
    statement: Migrated the C++ and Python back-end of Example AG's document storage application to Linux without disrupting its more than 350,000 customers.
    numbers: ["350,000"]
    status: confirmed
  - id: TEST-BUILD
    statement: Halved average build times for 55 developers by moving to Conan packaging.
    numbers: ["55"]
    status: confirmed
  - id: TEST-ZEROGC
    statement: Wrote zero-GC Java code for a low-latency order management system at Sample Bank.
    status: confirmed
  - id: TEST-UNCONFIRMED
    statement: Placeholder.
    status: needs-confirmation
    note: test
""")

from jobsearch import check  # noqa: E402  (must follow JOBSEARCH_DATA)

POSTING = "---\ncompany: Example AG\n---\nWe need a C++ engineer for distributed storage on Linux.\n"
GOOD = """---
to: Example AG
---
Dear Hiring Team,

I migrated the C++ and Python back-end of Example AG's document storage application to Linux without disrupting its more than 350,000 customers. [F:TEST-MIGRATE]

- I halved average build times for 55 developers by moving to Conan packaging. [F:TEST-BUILD]
- I wrote zero-GC Java code for a low-latency order management system at Sample Bank. [F:TEST-ZEROGC]

Your advert asks for a C++ engineer for distributed storage. [J]

Kind regards,

Your Name
"""


def run(draft, notes=None):
    d = TMP / "jobs" / "x"
    d.mkdir(parents=True, exist_ok=True)
    (d / "posting.md").write_text(POSTING)
    (d / "letter.draft.md").write_text(draft)
    if notes is not None:
        (d / "notes.md").write_text(notes)
    elif (d / "notes.md").exists():
        (d / "notes.md").unlink()
    return check.check(d)


class CheckTest(unittest.TestCase):
    def test_good_letter_passes(self):
        r = run(GOOD)
        self.assertEqual(r["errors"], [])
        self.assertEqual(r["cited"], ["TEST-BUILD", "TEST-MIGRATE", "TEST-ZEROGC"])

    def test_inflated_number_fails(self):
        r = run(GOOD.replace("350,000", "500,000"))
        self.assertTrue(any("NUMBER '500,000'" in e for e in r["errors"]))

    def test_untagged_claim_fails(self):
        r = run(GOOD.replace("I wrote zero-GC", "I have deep knowledge of AWS. I wrote zero-GC"))
        self.assertTrue(any(e.startswith("UNTAGGED") for e in r["errors"]))

    def test_unknown_fact_fails(self):
        r = run(GOOD.replace("[F:TEST-ZEROGC]", "[F:TEST-CORE-BANKING]"))
        self.assertTrue(any("UNKNOWN FACT TEST-CORE-BANKING" in e for e in r["errors"]))

    def test_upgraded_verb_fails(self):
        r = run(GOOD.replace("I migrated", "I led the migration of"))
        self.assertTrue(any("VERB 'led'" in e for e in r["errors"]))

    def test_banned_phrase_fails(self):
        r = run(GOOD.replace("Your advert asks", "As a world-class engineer, I see your advert asks"))
        self.assertTrue(any("BANNED" in e for e in r["errors"]))

    def test_number_word_needs_support(self):
        r = run(GOOD.replace("I wrote zero-GC", "Over two decades I wrote zero-GC"))
        self.assertTrue(any("NUMBER WORD 'decades'" in e for e in r["errors"]))

    def test_preference_needs_notes(self):
        draft = GOOD.replace("Kind regards,", "I would like to work in Zurich. [P]\n\nKind regards,")
        self.assertTrue(any("[P] used" in w for w in run(draft)["warnings"]))
        self.assertFalse(any("[P] used" in w for w in run(draft, notes="Prefer Zurich office.")["warnings"]))

    def test_needs_confirmation_warns(self):
        r = run(GOOD.replace("[F:TEST-ZEROGC]", "[F:TEST-ZEROGC,TEST-UNCONFIRMED]"))
        self.assertTrue(any("TEST-UNCONFIRMED (needs-confirmation)" in w for w in r["warnings"]))

    def test_standard_closing(self):
        closing = check.load_rules().get("standard_closing") or []
        self.assertTrue(closing, "letter-rules.yaml should define standard_closing")
        texts = [e["text"] if isinstance(e, dict) else e for e in closing]
        with_it = GOOD.replace("Kind regards,", "\n\n".join(texts) + "\n\nKind regards,")
        r = run(with_it)
        self.assertEqual(r["errors"], [])
        self.assertFalse(any("standard closing missing" in w for w in r["warnings"]))
        for e in closing:
            if isinstance(e, dict) and e.get("fact"):
                self.assertIn(e["fact"], r["cited"])
        self.assertTrue(any("standard closing missing" in w for w in run(GOOD)["warnings"]))

    def test_standard_closing_is_the_only_exemption(self):
        # the same banned wording in a drafted sentence still fails, even when tagged
        r = run(GOOD.replace("Your advert asks", "I have a track record of shipping fast. [F:TEST-ZEROGC] Your advert asks"))
        self.assertTrue(any("BANNED 'track record of'" in e for e in r["errors"]))
        # a near-miss of the standard text is an ordinary sentence again, so it needs a tag
        r = run(GOOD.replace("Kind regards,", "I'd like to be considered, I am great at everything.\n\nKind regards,"))
        self.assertTrue(any(e.startswith("UNTAGGED") for e in r["errors"]))

    def test_sign_off_variants_are_exempt(self):
        for sign_off in ["Kindest regards,", "Warm regards,", "With best wishes,", "Many thanks,", "Thank you,",
                         "Yours truly,", "Best,"]:
            r = run(GOOD.replace("Kind regards,", sign_off))
            self.assertFalse(any("UNTAGGED" in e for e in r["errors"]), sign_off)
        r = run(GOOD.replace("Kind regards,", "Kind regards, I am the best engineer you will find."))
        self.assertTrue(any("UNTAGGED" in e for e in r["errors"]))


if __name__ == "__main__":
    unittest.main()
