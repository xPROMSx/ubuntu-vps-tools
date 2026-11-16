"""Synthetic Git regressions; no production history or personal fixture identities."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import check_commit_metadata as policy

CHECKER = Path(__file__).with_name("check_commit_metadata.py").resolve()
PUBLIC = ("Fixture User", "123+fixture-user@users.noreply.github.com")
PRIVATE = ("Invented Private Fixture", "invented.private@example.test")


class CommitMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        self.git("init", "-q", "--initial-branch=main")
        self.tree = self.git("mktree", input="")
        self.serial = 0

    def git(self, *args, input=None, env=None):
        result = subprocess.run(["git", "-c", "commit.gpgsign=false", *args], cwd=self.repo,
                                input=input, text=True, capture_output=True,
                                env={**os.environ, **(env or {})}, check=True)
        return result.stdout.strip()

    def commit(self, parent=None, author=PUBLIC, committer=PUBLIC):
        self.serial += 1
        env = {"GIT_AUTHOR_NAME": author[0], "GIT_AUTHOR_EMAIL": author[1],
               "GIT_COMMITTER_NAME": committer[0], "GIT_COMMITTER_EMAIL": committer[1],
               "GIT_AUTHOR_DATE": "2000-01-01T00:00:00+00:00",
               "GIT_COMMITTER_DATE": "2000-01-01T00:00:00+00:00"}
        args = ["commit-tree", self.tree, "-m", f"Synthetic fixture {self.serial}"]
        if parent:
            args += ["-p", parent]
        return self.git(*args, env=env)

    def check(self, base, head, success=True, event="pull_request", repo=None):
        result = subprocess.run([sys.executable, str(CHECKER), "--event", event,
                                 "--base", base, "--head", head], cwd=repo or self.repo,
                                text=True, capture_output=True)
        self.assertEqual(result.returncode == 0, success, result.stdout + result.stderr)
        output = result.stdout + result.stderr
        self.assertNotIn(PRIVATE[0], output)
        self.assertNotIn(PRIVATE[1], output)
        return output

    def test_maintainer_and_alias(self):
        base = self.commit()
        for name in sorted(policy.MAINTAINER_NAMES):
            with self.subTest(name=name):
                identity = (name, policy.MAINTAINER_EMAIL)
                self.check(base, self.commit(base, identity, identity))

    def test_github_human_web_and_bots(self):
        base = self.commit()
        identities = (PUBLIC, ("Invented Legacy User", "fixture-legacy@users.noreply.github.com"),
                      ("GitHub", "noreply@github.com"), ("web-flow", "noreply@github.com"),
                      ("github-actions[bot]", "789+github-actions[bot]@users.noreply.github.com"),
                      ("fixture-bot[bot]", "456+fixture-bot[bot]@users.noreply.github.com"))
        for identity in identities:
            with self.subTest(identity=identity):
                self.check(base, self.commit(base, identity, identity))

    def test_rejected_roles_are_redacted(self):
        base = self.commit()
        for role, author, committer in (("author", PRIVATE, PUBLIC), ("committer", PUBLIC, PRIVATE)):
            with self.subTest(role=role):
                head = self.commit(base, author, committer)
                output = self.check(base, head, False)
                self.assertIn(f"commit {head} {role}", output)
                other = "committer" if role == "author" else "author"
                self.assertNotIn(f"commit {head} {other}", output)

    def test_mismatched_maintainer_and_narrow_domain_rules(self):
        base = self.commit()
        rejected = (("Proms", PRIVATE[1]), ("Proms", PUBLIC[1]),
                    ("Invented User", policy.MAINTAINER_EMAIL),
                    ("Invented User", "999+xPROMSx@users.noreply.github.com"),
                    ("Invented User", "fixture@users.noreply.github.com.example.test"),
                    ("Invented User", "fixture@github.com"),
                    ("Invented User", "noreply@github.com"),
                    ("Invented User", "456+fixture-bot[bot]@users.noreply.github.com"))
        for identity in rejected:
            with self.subTest(identity=identity):
                self.check(base, self.commit(base, identity, PUBLIC), False)

    def test_historical_metadata_is_not_revalidated(self):
        base = self.commit(author=PRIVATE, committer=PRIVATE)
        output = self.check(base, self.commit(base))
        self.assertIn("1 new PR commits", output)
        self.check(base, base)

    def test_multi_commit_and_diverged_base(self):
        ancestor = self.commit(author=PRIVATE)
        base = self.commit(ancestor, PRIVATE)
        first = self.commit(ancestor)
        head = self.commit(first)
        self.assertIn("2 new PR commits", self.check(base, head))
        bad = self.commit(head, committer=PRIVATE)
        self.assertIn(f"commit {bad} committer", self.check(base, bad, False))

    def test_invalid_missing_and_non_commit_endpoints(self):
        base = self.commit()
        for bad in ("", "main", "--all", "0" * 40, self.tree):
            with self.subTest(bad=bad):
                self.check(bad, base, False)
                self.check(base, bad, False)

    def test_missing_parent_object(self):
        missing = self.commit()
        base = self.commit(missing)
        head = self.commit(base)
        (self.repo / ".git" / "objects" / missing[:2] / missing[2:]).unlink()
        self.check(base, head, False)

    def test_shallow_history(self):
        base = self.commit()
        head = self.commit(base)
        self.git("update-ref", "refs/heads/main", head)
        shallow = Path(self.temporary.name) / "shallow"
        self.git("clone", "-q", "--depth=2", self.repo.as_uri(), str(shallow))
        self.check(base, head, False, repo=shallow)

    def test_commit_limit_is_not_a_partial_pass(self):
        base = self.commit()
        head = base
        # Lower only the imported policy limit in this process; production remains 1000.
        for _ in range(3):
            head = self.commit(head)
        code = (f"import sys; sys.path.insert(0,{str(CHECKER.parent)!r}); "
                "import check_commit_metadata as p; p.MAX_COMMITS=2; "
                f"sys.argv=['checker','--event','pull_request','--base',{base!r},'--head',{head!r}]; "
                "sys.exit(p.main())")
        result = subprocess.run([sys.executable, "-c", code], cwd=self.repo, capture_output=True)
        self.assertEqual(result.returncode, 1)
        self.assertNotIn(b"PASS", result.stdout)

    def test_push_skips_history_and_unknown_event_fails(self):
        base = self.commit(author=PRIVATE, committer=PRIVATE)
        self.assertIn("SKIP", self.check(base, base, event="push"))
        self.check(base, base, False, event="")


if __name__ == "__main__":
    unittest.main()
