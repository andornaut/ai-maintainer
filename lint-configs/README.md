# Lint configs

The canonical lint configuration for each language, and the sweep that reports
where a repository's copy has drifted from it.

Every repository carries its own copy, because none of these tools can inherit
from somewhere else: `golangci-lint` has no include mechanism at all, and
`ruff`'s `extend` takes a local path only. Copies drift, so this reports the
drift rather than pretending it cannot happen.

## Reconciling

A report is not a verdict. Drift runs both ways: a repository can fall behind,
and it can also be ahead, having reached a stricter position on its own. Read
the diff before deciding which side moves.

Where the copies are meant to differ, they differ in named places:

| Language | Per-repository, by design |
|---|---|
| Go | the `gci` import prefix, the `gosec` suppression list, exclusion rules a repository states are local |
| Python | `per-file-ignores`, `extend-include`, `extend-exclude`, and `target-version` where a repository states a floor |
| JavaScript | nothing: `eslint.config.base.mjs` is byte-identical everywhere, and each repository's own `eslint.config.mjs` applies it to its paths |
| Shell | `scandir` and `ignore_paths`, which name a repository's own vendored trees |

The sweep skips those and compares the rest.

A repository GitHub reports as holding Shell is also expected to carry the
ShellCheck step at all, not merely to match it where it exists: a gate that was
never added otherwise reads exactly like one that passed. `SHELL_EXEMPT` in
`check-drift.py` names the repositories that lint shell some other way, each
with the reason.

## Running it

    python3 lint-configs/check-drift.py            # report, exit 1 on drift
    python3 lint-configs/check-drift.py --repo gog # one repository

It reads each file through `gh api`, so it needs `gh` authenticated and clones
nothing.
