"""PR-only commit privacy policy. Email syntax is not proof of account ownership."""
import argparse
import os
import re
import subprocess
import sys

MAINTAINER_EMAIL = "102687702+xPROMSx@users.noreply.github.com"
MAINTAINER_NAMES = {"Proms", "xPROMSx"}
LOGIN = r"[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}"
NOREPLY = re.compile(rf"(?:[1-9][0-9]*\+)?({LOGIN})(\[bot\])?@users\.noreply\.github\.com")
MAX_COMMITS = 1000


def approved(name, email):
    if not name or name != name.strip() or any(ord(c) < 32 or ord(c) == 127 for c in name):
        return False
    if name.casefold() in {n.casefold() for n in MAINTAINER_NAMES} or email.casefold() == MAINTAINER_EMAIL.casefold():
        return name in MAINTAINER_NAMES and email == MAINTAINER_EMAIL
    if email == "noreply@github.com":
        return name in {"GitHub", "web-flow"}
    match = NOREPLY.fullmatch(email)
    if not match or match[1].casefold() == "xpromsx":
        return False
    return not match[2] or name == match[1] + "[bot]"


def git(*args):
    # Do not follow replace refs or mailmaps; inspect the real commit identities.
    result = subprocess.run(["git", "--no-replace-objects", *args],
                            capture_output=True, timeout=30)
    if result.returncode:
        raise ValueError("Git objects/history could not be validated")
    return result.stdout.decode("utf-8").rstrip("\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", default=os.environ.get("GITHUB_EVENT_NAME"))
    parser.add_argument("--base", default=os.environ.get("PR_BASE_SHA", ""))
    parser.add_argument("--head", default=os.environ.get("PR_HEAD_SHA", ""))
    args = parser.parse_args()
    if args.event == "push":
        print("SKIP: metadata validation is PR-only; push history is not revalidated")
        return 0
    if args.event != "pull_request":
        print("FAIL: unsupported or missing event", file=sys.stderr)
        return 1
    try:
        for revision in (args.base, args.head):
            if not re.fullmatch(r"[0-9a-fA-F]{40}", revision):
                raise ValueError("Both trusted PR endpoints must be full commit SHAs")
            if git("cat-file", "-t", revision) != "commit":
                raise ValueError("PR endpoints must reference commit objects")
        if git("rev-parse", "--is-shallow-repository") != "false":
            raise ValueError("Complete commit history is required on PRs")
        # Traverse both ancestries to reject missing parent objects, including at base.
        git("rev-list", "--quiet", args.base, args.head)
        commits = git("rev-list", f"--max-count={MAX_COMMITS + 1}",
                      f"{args.base}..{args.head}").splitlines()
        if len(commits) > MAX_COMMITS:
            raise ValueError("PR exceeds the 1000-commit validation limit")
        rejected = False
        for sha in commits:
            fields = git("show", "--no-patch", "--format=%an%x00%ae%x00%cn%x00%ce", sha).split("\0")
            if len(fields) != 4:
                raise ValueError("Commit metadata could not be parsed")
            for role, name, email in (("author", *fields[:2]), ("committer", *fields[2:])):
                if not approved(name, email):
                    # Never expose rejected identity values, even in a failing test log.
                    print(f"FAIL: commit {sha} {role} metadata is not approved", file=sys.stderr)
                    rejected = True
        if rejected:
            return 1
        print(f"PASS: metadata of {len(commits)} new PR commits (author and committer)")
        return 0
    except (ValueError, UnicodeError, OSError, subprocess.TimeoutExpired):
        print("FAIL: invalid PR endpoints or incomplete/unreadable Git history; validation not completed",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
