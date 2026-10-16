"""Recruiter conversations: paste, parse, merge, link jobs, log efforts."""
import os
import shutil
import tempfile
import unittest

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import conversations, efforts, jobs, paths, web  # noqa: E402  (must follow JOBSEARCH_DATA)

OLDER = """On Thursday, 10/15/26 at 12:59 Adam Luoranen <adam@gulenko.com> wrote:

Hello Jane, two roles: https://www.akros.ch/jobs/java-software-engineer-fullstack/zurich/
and https://www.adesso.ch/de_ch/jobs/Senior-Java-j2934.html

On October 14, 2026 at 17:20, Jane Doe (jane.doe@example.ch) wrote:
Dear Adam, please find my updated CV attached.

Sent from Proton Mail for Android.

-------- Original Message --------
On Wednesday, 10/14/26 at 17:15 Adam Luoranen <adam@gulenko.com> wrote:

Sehr geehrte Frau Doe, bitte schicken Sie uns Ihren Lebenslauf.
"""
NEWER = """On Thursday, October 15th, 2026 at 1:39 PM, Jane Doe <jane.doe@example.ch> wrote:
Fair enough. Please proceed for these two.

-------- Original Message --------
""" + OLDER


class Conversations(unittest.TestCase):
    def setUp(self):
        shutil.rmtree(conversations.DIR, ignore_errors=True)
        shutil.rmtree(paths.JOBS, ignore_errors=True)
        paths.SEARCH.mkdir(parents=True, exist_ok=True)
        (paths.SEARCH / "config.yaml").write_text("sources: {}\nfilters: {title_include: [x], title_exclude: [], locations: [x]}\nscore_terms: {}\n")

    def test_header_formats(self):
        self.assertEqual(conversations.parse_when("Thursday, October 15th, 2026 at 1:39 PM"), ("2026-10-15", "13:39"))
        self.assertEqual(conversations.parse_when("Thursday, 10/15/26 at 13:36"), ("2026-10-15", "13:36"))
        self.assertEqual(conversations.parse_when("Do., 15. Okt. 2026 um 09:05"), ("2026-10-15", "09:05"))
        self.assertEqual(conversations.parse_when("15.10.2026 14:00"), ("2026-10-15", "14:00"))
        msgs = conversations.parse_thread(NEWER, ["Jane Doe"])
        self.assertEqual([(m["date"], m["time"], m["me"]) for m in msgs],
                         [("2026-10-14", "17:15", False), ("2026-10-14", "17:20", True),
                          ("2026-10-15", "12:59", False), ("2026-10-15", "13:39", True)])
        self.assertNotIn("Sent from Proton Mail", msgs[1]["body"])
        by_email = conversations.parse_thread(NEWER, [], ["Jane.Doe@example.ch"])  # address alone is enough
        self.assertEqual([m["me"] for m in by_email], [m["me"] for m in msgs])

    def test_paste_then_longer_paste_merges(self):
        conv_id, total, new = conversations.save_paste(OLDER)
        self.assertEqual((conv_id, total, new), ("2026-10-14-gulenko-adam-luoranen", 3, 3))
        self.assertEqual(conversations.save_paste(OLDER), (conv_id, 3, 0))  # same paste again: nothing new
        self.assertEqual(conversations.save_paste(NEWER), (conv_id, 4, 1))
        c = conversations.load(conv_id)
        self.assertEqual((c["contact"], c["email"], c["agency"]), ("Adam Luoranen", "adam@gulenko.com", "Gulenko"))
        self.assertEqual(len(c["pastes"]), 2)  # both pastes kept word for word
        self.assertEqual(len(c["links"]), 2)

    def test_two_jobs_from_one_conversation_log_via_the_recruiter(self):
        conv_id, _, _ = conversations.save_paste(NEWER)
        client = web.create_app().test_client()
        client.get("/")
        with client.session_transaction() as s:
            csrf = s["csrf"]
        for company, url in [("Akros", "https://www.akros.ch/jobs/java-software-engineer-fullstack/zurich/"),
                             ("adesso", "https://www.adesso.ch/de_ch/jobs/Senior-Java-j2934.html")]:
            client.post(f"/recruiters/{conv_id}/add-job", data={"csrf": csrf, "url": url, "company": company,
                                                                 "title": "Java Engineer", "location": "Zürich"})
        c = conversations.load(conv_id)
        self.assertEqual(len(c["jobs"]), 2)
        akros = c["jobs"][0]
        self.assertEqual(jobs.load(akros)["via"], conv_id)
        self.assertIn("Via recruiter", client.get(f"/job/{akros}").get_data(as_text=True))
        client.post(f"/job/{akros}/status", data={"csrf": csrf, "status": "applied"})
        row = [r for r in efforts.read() if r["job_id"] == akros][0]
        self.assertEqual((row["company"], row["contact_person"], row["contact_email"]),
                         ("Akros via Gulenko", "Adam Luoranen", "adam@gulenko.com"))
        client.post(f"/recruiters/{conv_id}/log-contact", data={"csrf": csrf, "date": "2026-10-14", "method": "phone"})
        contact = [r for r in efforts.read() if r["type"] == "recruiter-contact" and conv_id in r["notes"]]
        self.assertEqual((contact[0]["company"], contact[0]["method"]), ("Gulenko", "phone"))
        page = client.get(f"/recruiters/{conv_id}").get_data(as_text=True)
        self.assertIn("saved and linked", page)
        self.assertIn("Please proceed for these two", page)

    def test_not_a_thread(self):
        with self.assertRaises(ValueError):
            conversations.save_paste("just some text")


if __name__ == "__main__":
    unittest.main()
