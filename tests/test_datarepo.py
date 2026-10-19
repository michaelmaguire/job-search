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


class RecordInCodeRepo(unittest.TestCase):
    """data/ as a submodule of a throwaway code repo: pushing data also records the new pointer there."""

    def setUp(self):
        root = Path(tempfile.mkdtemp())
        self.data_remote, self.code_remote, self.code = root / "data.git", root / "code.git", root / "code"
        self.data = self.code / "data"
        sh(root, "git", "init", "-q", "--bare", "-b", "main", str(self.data_remote))
        sh(root, "git", "init", "-q", "--bare", "-b", "main", str(self.code_remote))
        sh(root, "git", "init", "-q", "-b", "main", str(self.code))
        sh(root, "git", "clone", "-q", str(self.data_remote), str(self.data))
        for repo in (self.code, self.data):
            for k, v in [("user.name", "Test"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")]:
                sh(repo, "git", "config", k, v)
        (self.data / "efforts.csv").write_text("date\n")
        sh(self.data, "git", "add", "-A")
        sh(self.data, "git", "commit", "-qm", "init")
        sh(self.data, "git", "push", "-q", "-u", "origin", "main")
        (self.code / "app.py").write_text("v1\n")
        (self.code / "other.py").write_text("v1\n")
        sh(self.code, "git", "add", "app.py", "other.py", "data")  # data -> gitlink
        sh(self.code, "git", "commit", "-qm", "init")
        sh(self.code, "git", "remote", "add", "origin", str(self.code_remote))
        sh(self.code, "git", "push", "-q", "-u", "origin", "main")
        self.patches = [mock.patch.object(datarepo, "DATA", self.data), mock.patch.object(datarepo, "REPO", self.code)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()

    def test_push_records_only_the_pointer_and_pushes_it(self):
        (self.code / "app.py").write_text("v2 staged\n")
        sh(self.code, "git", "add", "app.py")
        (self.code / "other.py").write_text("v2 unstaged\n")
        (self.data / "efforts.csv").write_text("date\n2026-10-19\n")
        ok, summary = datarepo.commit_and_push("log effort")
        self.assertTrue(ok, summary)
        self.assertIn("recorded data", summary)
        self.assertIn("pushed it", summary)
        data_head = sh(self.data, "git", "rev-parse", "HEAD")
        self.assertEqual(sh(self.code_remote, "git", "rev-parse", "main:data"), data_head)
        # the commit holds the pointer and nothing else
        self.assertEqual(sh(self.code, "git", "show", "--name-only", "--format=", "HEAD"), "data")
        # other code changes are untouched: still staged / still unstaged, not committed
        self.assertEqual(sh(self.code, "git", "diff", "--cached", "--name-only"), "app.py")
        self.assertEqual(sh(self.code, "git", "diff", "--name-only"), "other.py")
        self.assertEqual(sh(self.code, "git", "show", "HEAD:app.py"), "v1")
        self.assertFalse(datarepo.pointer_stale())

    def test_other_unpushed_code_commits_are_not_pushed(self):
        (self.code / "app.py").write_text("v2\n")
        sh(self.code, "git", "commit", "-qam", "my unpushed work")
        (self.data / "efforts.csv").write_text("changed\n")
        ok, summary = datarepo.commit_and_push()
        self.assertTrue(ok, summary)
        self.assertIn("not pushed", summary)
        self.assertEqual(sh(self.code, "git", "log", "-1", "--format=%s"), f"Update data to {sh(self.data, 'git', 'rev-parse', '--short', 'HEAD')}")
        self.assertEqual(sh(self.code_remote, "git", "log", "-1", "--format=%s", "main"), "init")

    def test_stale_pointer_shows_and_is_recorded_with_nothing_to_push(self):
        (self.data / "efforts.csv").write_text("pushed by hand\n")
        sh(self.data, "git", "commit", "-qam", "by hand")
        sh(self.data, "git", "push", "-q")
        st = datarepo.status()
        self.assertTrue(st["pointer_stale"])
        ok, summary = datarepo.commit_and_push()
        self.assertTrue(ok, summary)
        self.assertIn("recorded data", summary)
        self.assertFalse(datarepo.status()["pointer_stale"])

    def test_failed_data_push_does_not_touch_code_repo(self):
        other = self.code.parent / "other"
        sh(self.code.parent, "git", "clone", "-q", str(self.data_remote), str(other))
        sh(other, "git", "-c", "user.name=O", "-c", "user.email=o@example.com", "commit", "-q", "--allow-empty", "-m", "elsewhere")
        sh(other, "git", "push", "-q")
        before = sh(self.code, "git", "rev-parse", "HEAD")
        (self.data / "efforts.csv").write_text("changed\n")
        ok, _ = datarepo.commit_and_push()
        self.assertFalse(ok)
        self.assertEqual(sh(self.code, "git", "rev-parse", "HEAD"), before)


if __name__ == "__main__":
    unittest.main()
