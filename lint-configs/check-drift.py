#!/usr/bin/env python3
"""Report where a repository's lint config has drifted from the canonical one.

None of these tools can inherit a config from elsewhere, so every repository
carries a copy and copies drift. This reads each copy through `gh api`, compares
it with lint-configs/, and prints what differs.

It reports rather than repairs. Drift runs both ways: a repository can fall
behind, and it can also be ahead, having reached a stricter position on its own,
which is worth adopting rather than reverting. Deciding which is which needs the
diff, so the diff is what this prints.

Exits 1 when anything has drifted, 0 when nothing has.
"""

import argparse
import base64
import difflib
import subprocess
import sys
from pathlib import Path

import tomllib
import yaml

CANON = Path(__file__).parent

# Where a copy is meant to differ. Everything else is compared.
GO_LOCAL = (
    ("linters", "settings", "gosec", "excludes"),
    ("formatters", "settings", "gci", "sections"),
)
PYTHON_LOCAL = (
    ("lint", "per-file-ignores"),
    ("extend-include",),
    ("extend-exclude",),
    ("target-version",),
    # The names a sandbox injects, which only the repository running in one has.
    ("builtins",),
)
SHELL_LOCAL = ("scandir", "ignore_paths")


def gh(*args):
    """Run gh and return stdout, or None when it fails (a missing file, usually)."""
    result = subprocess.run(  # noqa: S603
        ["gh", *args],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def repositories():
    """Every repository owned here that is neither archived nor a fork.

    The public listing rather than the authenticated one: a workflow's
    GITHUB_TOKEN is not a user, so `user/repos` returns nothing for it. These
    repositories are all public, and the two endpoints return the same set.
    """
    out = gh(
        "api",
        "users/andornaut/repos?per_page=100&type=owner",
        "--paginate",
        "--jq",
        ".[] | select(.archived==false and .fork==false) | .name",
    )
    if out is None:
        sys.exit("cannot list repositories: is gh authenticated?")
    return sorted(out.split())


def fetch(repo, path):
    """A repository's file at its default branch, or None when it has none."""
    return gh("api", f"repos/andornaut/{repo}/contents/{path}", "--jq", ".content")


def decode(content):
    return base64.b64decode(content).decode()


def strip(tree, paths):
    """Drop the declared-local keys so what remains is the shared part."""
    for path in paths:
        node = tree
        for key in path[:-1]:
            node = node.get(key) if isinstance(node, dict) else None
            if node is None:
                break
        if isinstance(node, dict):
            node.pop(path[-1], None)
    return tree


def local_rules(tree):
    """Exclusion rules a repository has marked as its own are not drift."""
    rules = tree.get("linters", {}).get("exclusions", {}).get("rules")
    if isinstance(rules, list):
        tree["linters"]["exclusions"]["rules"] = [
            r for r in rules if "govet" not in (r.get("linters") or []) and "tparallel" not in (r.get("linters") or [])
        ]
    return tree


def compare_structured(name, canon_text, repo_text, loader, locals_):
    canon = strip(loader(canon_text), locals_)
    theirs = strip(loader(repo_text), locals_)
    if name == "go":
        canon, theirs = local_rules(canon), local_rules(theirs)
    if canon == theirs:
        return None
    return "\n".join(
        difflib.unified_diff(
            yaml.dump(canon, sort_keys=True).splitlines(),
            yaml.dump(theirs, sort_keys=True).splitlines(),
            "canon",
            "repository",
            lineterm="",
        )
    )


def compare_bytes(canon_text, repo_text):
    if canon_text == repo_text:
        return None
    return "\n".join(
        difflib.unified_diff(canon_text.splitlines(), repo_text.splitlines(), "canon", "repository", lineterm="")
    )


def shellcheck_step(text):
    """The ShellCheck step out of a workflow, as a dict, or None when absent."""
    document = yaml.safe_load(text)
    for job in (document.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if "action-shellcheck" in str(step.get("uses", "")):
                return step
    return None


def check(repo):
    """Every drifted artifact in one repository, as (label, diff) pairs."""
    found = []

    for label, path, loader, locals_ in (
        ("go", ".golangci.yml", yaml.safe_load, GO_LOCAL),
        ("python", "ruff.toml", tomllib.loads, PYTHON_LOCAL),
    ):
        content = fetch(repo, path)
        if content is None:
            continue
        canon_name = "golangci.yml" if label == "go" else "ruff.toml"
        canon_text = (CANON / label / canon_name).read_text()
        diff = compare_structured(label, canon_text, decode(content), loader, locals_)
        if diff:
            found.append((path, diff))

    content = fetch(repo, "eslint.config.base.mjs")
    if content is not None:
        canon_text = (CANON / "javascript" / "eslint.config.base.mjs").read_text()
        diff = compare_bytes(canon_text, decode(content))
        if diff:
            found.append(("eslint.config.base.mjs", diff))

    content = fetch(repo, ".github/workflows/test.yml")
    if content is not None:
        theirs = shellcheck_step(decode(content))
        if theirs is not None:
            canon_step = yaml.safe_load((CANON / "shell" / "shellcheck-step.yml").read_text())[0]
            for key in SHELL_LOCAL:
                (canon_step.get("with") or {}).pop(key, None)
                (theirs.get("with") or {}).pop(key, None)
            if canon_step != theirs:
                diff = "\n".join(
                    difflib.unified_diff(
                        yaml.dump(canon_step, sort_keys=True).splitlines(),
                        yaml.dump(theirs, sort_keys=True).splitlines(),
                        "canon",
                        "repository",
                        lineterm="",
                    )
                )
                found.append(("ShellCheck step", diff))

    return found


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", help="check one repository rather than all of them")
    args = parser.parse_args()

    names = [args.repo] if args.repo else repositories()
    drifted = 0
    for name in names:
        for label, diff in check(name):
            drifted += 1
            print(f"\n===== {name}: {label}")
            print(diff)

    if drifted:
        print(f"\n{drifted} config(s) differ from lint-configs/. Read the diff before deciding")
        print("which side moves: a repository ahead of canon is worth adopting, not reverting.")
        return 1
    print(f"{len(names)} repositories checked, no drift.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
