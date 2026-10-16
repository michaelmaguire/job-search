"""Commit & push of the data repo, against a throwaway repo and a local bare 'remote'."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

os.environ["JOBSEARCH_DATA"] = os.environ.setdefault("JOBSEARCH_TEST_DATA", tempfile.mkdtemp())  # one temp dir for all test modules
os.environ["JOBSEARCH_SNAPSHOT"] = "off"  # no Chrome during tests
os.environ["JOBSEARCH_CONFIG"] = os.environ.setdefault("JOBSEARCH_TEST_CONFIG", tempfile.mkdtemp())  # never your real credentials

from jobsearch import datarepo  # noqa: E402  (must follow JOBSEARCH_DATA)


def sh(cwd, *args):
    return subprocess.run(args, cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class CommitAndPush(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.remote, self.repo = root / "remote.git", root / "data"
        sh(root, "git", "init", "-q", "--bare", "-b", "main", str(self.remote))
        sh(root, "git", "clone", "-q", str(self.remote), str(self.repo))
        for k, v in [("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")]:
            sh(self.repo, "git", "config", k, v)
        (self.repo / "efforts.csv").write_text("date\n")
        sh(self.repo, "git", "add", "-A")
        sh(self.repo, "git", "commit", "-qm", "init")
        sh(self.repo, "git", "push", "-q", "-u", "origin", "main")
        self.patch = mock.patch.object(datarepo, "DATA", self.repo)
        self.patch.start()

    def tearDown(self):
        self.patch.stop()

    def test_commit_and_push_everything(self):
        (self.repo / "jobs" / "2026-10-15-acme-engineer").mkdir(parents=True)
        (self.repo / "jobs" / "2026-10-15-acme-engineer" / "posting.md").write_text("x")
        (self.repo / "efforts.csv").write_text("date\n2026-10-15\n")
        st = datarepo.status()
        self.assertEqual((st["branch"], len(st["changes"])), ("main", 2))
        self.assertEqual(datarepo.default_message(st["changes"]), "efforts, acme-engineer (2 files)")
        ok, summary = datarepo.commit_and_push()
        self.assertTrue(ok, summary)
        self.assertEqual(sh(self.remote, "git", "log", "-1", "--format=%s", "main"), "efforts, acme-engineer (2 files)")
        self.assertEqual(datarepo.status()["changes"], [])
        self.assertEqual(datarepo.commit_and_push(), (True, "nothing to commit or push - data/ is up to date"))

    def test_rejected_push_keeps_commit_locally(self):
        other = self.repo.parent / "other"
        sh(self.repo.parent, "git", "clone", "-q", str(self.remote), str(other))
        sh(other, "git", "-c", "user.name=O", "-c", "user.email=o@example.com", "commit", "-q", "--allow-empty", "-m", "elsewhere")
        sh(other, "git", "push", "-q")
        (self.repo / "efforts.csv").write_text("changed\n")
        ok, summary = datarepo.commit_and_push("my message")
        self.assertFalse(ok)
        self.assertIn("commit kept locally", summary)
        self.assertIn("pull", summary)
        self.assertEqual(sh(self.repo, "git", "log", "-1", "--format=%s"), "my message")
        self.assertEqual(datarepo.status()["ahead"], 1)

    def test_detached_head_is_refused(self):
        sh(self.repo, "git", "checkout", "-q", "--detach")
        (self.repo / "efforts.csv").write_text("changed\n")
        ok, summary = datarepo.commit_and_push()
        self.assertFalse(ok)
        self.assertIn("detached HEAD", summary)


if __name__ == "__main__":
    unittest.main()
