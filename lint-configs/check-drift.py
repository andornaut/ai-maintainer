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
import json
import subprocess
import sys
import tomllib
from pathlib import Path

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
# The Markdown ignore list names a repository's own test data, which is meant to
# differ, so it is not compared whole. What canon names in it is still required:
# dropped_ignores below reports an entry that went missing. The step takes no
# per-repository input, so it is compared whole.
MARKDOWN_LOCAL = (("ignores",),)

# Repositories that hold shell and are meant to have no canonical ShellCheck
# step. Everything else GitHub reports as Shell is expected to carry one: the
# comparison below finds the step and skips a repository that has none, so a
# gate that was never added reads exactly like a gate that passed.
SHELL_EXEMPT = {
    # tests/lint.sh runs ShellCheck itself. It renders Jinja templates to a
    # temporary copy before checking them, which the action cannot do.
    "ansible-ctrl",
    # The only shell here is under fixtures/, which the suite feeds to the
    # program under test rather than running.
    "filectrl",
}


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


def prettier_entries(tree):
    """The lint-staged entries that run prettier, which every repository shares.

    The eslint entries name the script types a repository actually holds, so they
    are its own. The prettier entry is not: a hand-written type list is how the
    hook came to cover less than `prettier --check .` does, and `*` with
    --ignore-unknown is what keeps the two the same set.
    """
    return {glob: cmd for glob, cmd in tree.items() if "prettier" in str(cmd)}


def dropped_ignores(canon_text, repo_text):
    """Canonical Markdown ignore entries a repository no longer names.

    Adding to this list is a local decision, which is why the key is not
    compared whole. Dropping from it is not: node_modules is what keeps a local
    run from walking a dependency tree that CI's checkout does not have, and the
    agent instruction files are local-only, so a copy that stops naming them
    starts linting files that are not in the repository.
    """
    canon = yaml.safe_load(canon_text).get("ignores") or []
    theirs = set(yaml.safe_load(repo_text).get("ignores") or [])
    return [entry for entry in canon if entry not in theirs]


def local_rules(tree):
    """Exclusion rules a repository has marked as its own are not drift."""
    rules = tree.get("linters", {}).get("exclusions", {}).get("rules")
    if isinstance(rules, list):
        tree["linters"]["exclusions"]["rules"] = [
            r for r in rules if "govet" not in (r.get("linters") or []) and "tparallel" not in (r.get("linters") or [])
        ]
    return tree


def diff_trees(canon, theirs):
    """A unified diff of two parsed configs, rendered through yaml for reading."""
    return "\n".join(
        difflib.unified_diff(
            yaml.dump(canon, sort_keys=True).splitlines(),
            yaml.dump(theirs, sort_keys=True).splitlines(),
            "canon",
            "repository",
            lineterm="",
        )
    )


def compare_structured(name, canon_text, repo_text, loader, locals_):
    canon = strip(loader(canon_text), locals_)
    theirs = strip(loader(repo_text), locals_)
    if name == "go":
        canon, theirs = local_rules(canon), local_rules(theirs)
    if canon == theirs:
        return None
    return diff_trees(canon, theirs)


def compare_bytes(canon_text, repo_text):
    if canon_text == repo_text:
        return None
    return "\n".join(
        difflib.unified_diff(canon_text.splitlines(), repo_text.splitlines(), "canon", "repository", lineterm="")
    )


def workflow_step(text, action):
    """The step running `action` out of a workflow, as a dict, or None when absent."""
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        return None
    for job in (document.get("jobs") or {}).values():
        for step in job.get("steps") or []:
            if action in str(step.get("uses", "")):
                return step
    return None


def holds_shell(repo):
    """Whether GitHub detects shell in the repository.

    The languages endpoint rather than a sweep for *.sh: most of these scripts
    carry no extension, and linguist reads the shebang.

    One name per line, compared after splitting on lines: a name can hold a
    space, and gh ends its output with a newline that would otherwise ride along
    on the last name and stop it matching.
    """
    out = gh("api", f"repos/andornaut/{repo}/languages", "--jq", "keys[]")
    return "Shell" in (out or "").splitlines()


def workflow_names(repo):
    """Every workflow file name, so a step is found wherever it was put.

    Named files rather than test.yml alone: a step that moves is otherwise
    skipped silently, which reads the same as having no drift.
    """
    out = gh("api", f"repos/andornaut/{repo}/contents/.github/workflows", "--jq", ".[].name")
    return [n for n in (out or "").split() if n.endswith((".yml", ".yaml"))]


def check(repo):
    """Every drifted artifact in one repository, as (label, diff) pairs."""
    found = []

    present = set()
    for label, path, canon_name, loader, locals_ in (
        ("go", ".golangci.yml", "golangci.yml", yaml.safe_load, GO_LOCAL),
        ("python", "ruff.toml", "ruff.toml", tomllib.loads, PYTHON_LOCAL),
        ("markdown", ".markdownlint-cli2.yaml", "markdownlint-cli2.yaml", yaml.safe_load, MARKDOWN_LOCAL),
    ):
        content = fetch(repo, path)
        if content is None:
            continue
        present.add(label)
        canon_text = (CANON / label / canon_name).read_text()
        repo_text = decode(content)
        diff = compare_structured(label, canon_text, repo_text, loader, locals_)
        if diff:
            found.append((path, diff))
        if label == "markdown":
            missing = dropped_ignores(canon_text, repo_text)
            if missing:
                found.append((path, "ignores no longer names: " + ", ".join(missing)))

    # Reported rather than skipped, on the same reasoning as the ShellCheck step
    # below. Go and Python configs are skipped where the language is absent, but
    # every repository here holds at least a README.md, so a missing Markdown
    # config is a gate nobody added rather than one that does not apply.
    if "markdown" not in present:
        found.append((".markdownlint-cli2.yaml", "no markdownlint config, and every repository here holds Markdown"))

    content = fetch(repo, "eslint.config.base.mjs")
    if content is not None:
        canon_text = (CANON / "javascript" / "eslint.config.base.mjs").read_text()
        diff = compare_bytes(canon_text, decode(content))
        if diff:
            found.append(("eslint.config.base.mjs", diff))

    content = fetch(repo, ".lintstagedrc")
    if content is not None:
        canon = prettier_entries(json.loads((CANON / "javascript" / "lintstagedrc").read_text()))
        theirs = prettier_entries(json.loads(decode(content)))
        if canon != theirs:
            found.append((".lintstagedrc", diff_trees(canon, theirs)))
    elif fetch(repo, "package.json") is not None:
        # Reported rather than skipped, on the reasoning the step checks below
        # share: a repository that never added the hook reads exactly like one
        # whose hook matched.
        found.append((".lintstagedrc", "no lint-staged config, and the repository has a package.json"))

    stepped = False
    md_stepped = False
    for name in workflow_names(repo):
        content = fetch(repo, f".github/workflows/{name}")
        if content is None:
            continue
        text = decode(content)

        theirs = workflow_step(text, "action-shellcheck")
        if theirs is not None:
            stepped = True
            canon_step = yaml.safe_load((CANON / "shell" / "shellcheck-step.yml").read_text())[0]
            for key in SHELL_LOCAL:
                (canon_step.get("with") or {}).pop(key, None)
                (theirs.get("with") or {}).pop(key, None)
            if canon_step != theirs:
                found.append((f"ShellCheck step ({name})", diff_trees(canon_step, theirs)))

        theirs = workflow_step(text, "markdownlint-cli2-action")
        if theirs is not None:
            md_stepped = True
            canon_step = yaml.safe_load((CANON / "markdown" / "markdownlint-step.yml").read_text())[0]
            if canon_step != theirs:
                found.append((f"markdownlint step ({name})", diff_trees(canon_step, theirs)))

    # Reported rather than skipped. Comparing a step only where one exists says
    # nothing about a repository that holds shell and never added the step, and
    # that repository is the one worth hearing about. SHELL_EXEMPT names the
    # ones that lint shell some other way or deliberately do not.
    if not stepped and repo not in SHELL_EXEMPT and holds_shell(repo):
        found.append(("ShellCheck step", "no ShellCheck step in any workflow, and the repository holds shell"))

    if not md_stepped:
        found.append(
            ("markdownlint step", "no markdownlint step in any workflow, and every repository here holds Markdown")
        )

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
