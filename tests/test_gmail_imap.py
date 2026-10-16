"""Gmail over IMAP (fake server): login saves credentials privately, drafts replace earlier ones, search, status."""
import email
import email.policy
import os
import tempfile
import unittest
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import gmail, gmail_imap  # noqa: E402  (must follow JOBSEARCH_DATA)

INBOX_MSG = b"From: =?utf-8?q?Z=C3=BCrich_Talent?= <talent@example.ch>\r\nSubject: Your application\r\n" \
            b"Date: Fri, 16 Oct 2026 09:00:00 +0200\r\n\r\nThank you   for applying.\r\n"


class FakeIMAP:
    """Just enough of imaplib.IMAP4_SSL: German folder names, to show they're found by flag."""
    folders = {}

    def __init__(self, host):
        self.selected = None

    def login(self, user, password):
        if password != "abcdabcdabcdabcd":
            raise gmail_imap.imaplib.IMAP4.error("[AUTHENTICATIONFAILED] Invalid credentials")
        return "OK", [b""]

    def logout(self):
        return "BYE", [b""]

    def list(self):
        return "OK", [b'(\\HasNoChildren) "/" "INBOX"', b'(\\All \\HasNoChildren) "/" "[Gmail]/Alle Nachrichten"',
                      b'(\\Drafts \\HasNoChildren) "/" "[Gmail]/Entw&APw-rfe"', b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Papierkorb"']

    def select(self, name, readonly=False):
        self.selected = name.strip('"')
        return "OK", [b"1"]

    def append(self, folder, flags, date, raw):
        self.folders.setdefault(folder.strip('"'), []).append(raw)
        return "OK", [b"APPENDUID"]

    def uid(self, command, *args):
        box = self.folders.setdefault(self.selected, [])
        if command == "SEARCH" and args[0] == "HEADER":
            want = args[2].strip('"')
            return "OK", [" ".join(str(i + 1) for i, m in enumerate(box) if want.encode() in m).encode()]
        if command == "SEARCH":
            hits = [i + 1 for i, m in enumerate(box) if args[0] == "ALL" or args[1].strip('"').encode() in m]
            return "OK", [" ".join(map(str, hits)).encode()]
        if command == "FETCH":
            return "OK", [(b"1 (BODY[] {n}", box[int(args[0]) - 1]), b")"]
        if command == "MOVE":
            moved = [box[int(i) - 1] for i in args[0].split(",")]
            self.folders[self.selected] = [m for m in box if m not in moved]
            self.folders.setdefault(args[1].strip('"'), []).extend(moved)
            return "OK", [b""]
        raise AssertionError(command)


class GmailIMAP(unittest.TestCase):
    def setUp(self):
        FakeIMAP.folders = {"[Gmail]/Alle Nachrichten": [INBOX_MSG]}
        self.patch = mock.patch.object(gmail_imap.imaplib, "IMAP4_SSL", FakeIMAP)
        self.patch.start()
        gmail.forget_status()

    def tearDown(self):
        self.patch.stop()
        gmail_imap.config_path().unlink(missing_ok=True)
        gmail.forget_status()

    def test_wrong_password_saves_nothing(self):
        with self.assertRaises(SystemExit) as e:
            gmail_imap.login("me@gmail.com", "wrong")
        self.assertIn("refused", str(e.exception))
        self.assertFalse(gmail_imap.configured())

    def test_login_then_drafts_and_search_go_over_imap(self):
        self.assertEqual(gmail_imap.login("me@gmail.com", "abcd abcd abcd abcd"), "me@gmail.com")
        self.assertEqual(oct(gmail_imap.config_path().stat().st_mode & 0o777), "0o600")
        self.assertEqual(gmail.status(), (True, "connected as me@gmail.com (IMAP)"))

        first = gmail.create_draft("jobs@acme.example", "Application", "Hello")
        second = gmail.create_draft("jobs@acme.example", "Application", "Hello again", draft_id=first)
        self.assertIs(type(second), str)  # it goes into email.yaml via yaml.safe_dump
        drafts = FakeIMAP.folders["[Gmail]/Entw&APw-rfe"]
        self.assertEqual(len(drafts), 1)  # the earlier draft went to Trash, not left beside the new one
        self.assertEqual(len(FakeIMAP.folders["[Gmail]/Papierkorb"]), 1)
        msg = email.message_from_bytes(drafts[0], policy=email.policy.default)
        self.assertEqual((msg["Message-ID"], msg["From"], msg["To"]), (second, "me@gmail.com", "jobs@acme.example"))
        self.assertIn("Hello again", msg.get_content())
        gmail.create_draft("x@acme.example", "Other", "Hi", draft_id="r-123456")  # an OAuth id: nothing to replace
        self.assertEqual(len(FakeIMAP.folders["[Gmail]/Entw&APw-rfe"]), 2)

        [m] = gmail.messages("application", 5)
        self.assertEqual((m["from"], m["subject"], m["snippet"]),
                         ("Zürich Talent <talent@example.ch>", "Your application", "Thank you for applying."))

    def test_bad_saved_password_shows_in_status(self):
        gmail_imap.login("me@gmail.com", "abcdabcdabcdabcd")
        gmail_imap.config_path().write_text("address: me@gmail.com\napp_password: stale\n")
        ok, why = gmail.status()
        self.assertFalse(ok)
        self.assertIn("login --imap", why)


if __name__ == "__main__":
    unittest.main()
