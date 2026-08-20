# Lint configs

The canonical lint configuration for each language, plus the two files that are
meant to be byte-identical everywhere, and the sweep that reports where a
repository's copy has drifted from any of them.

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
| --- | --- |
| Go | the `gci` import prefix, the `gosec` suppression list, exclusion rules a repository states are local |
| Python | `per-file-ignores`, `extend-include`, `extend-exclude`, and `target-version` where a repository states a floor |
| JavaScript | the eslint entries in `.lintstagedrc`, which name the script types a repository holds; `eslint.config.base.mjs` is byte-identical everywhere, and each repository's own `eslint.config.mjs` applies it to its paths |
| Shell | `scandir` and `ignore_paths`, which name a repository's own vendored trees |
| Markdown | `ignores`, where a repository adds its own test data to the shared entries. Additions only: an entry canon names and a copy does not is reported |

The sweep skips those and compares the rest.

A repository GitHub reports as holding Shell is also expected to carry the
ShellCheck step at all, not merely to match it where it exists: a gate that was
never added otherwise reads exactly like one that passed. `SHELL_EXEMPT` in
`check-drift.py` names the repositories that lint shell some other way, each
with the reason.

Markdown is checked for presence the same way, and has no exemption list: every
repository here carries at least a `README.md`, so both `.markdownlint-cli2.yaml`
and the step are expected everywhere. The Go and Python configs are skipped where
the language is absent, and reported where GitHub detects the language and the
config is missing, read from the same languages endpoint the Shell check uses.

A repository with a `package.json` is expected to carry a `.lintstagedrc`, and its
prettier entry to be `*` with `--ignore-unknown`. Only that entry is compared. The
hook and CI have to cover one set of files: a hand-written list of types is how the
hook came to check less than `prettier --check .` does, missing the
`.markdownlint-cli2.yaml` every repository carries.

The same repository is expected to carry `.husky/pre-commit`, compared byte for
byte: it is one line, `npx lint-staged`, and every difference is a difference in
what the hook runs.

`.github/workflows/ai-attributions.yml` is compared byte for byte in every
repository, rather than by step as the ShellCheck and markdownlint gates are. It
is the attribution gate's own configuration, so a copy that quietly lost
`agents-files`, `emdashes` or `fetch-depth` still runs, still reports success, and
checks less than the others do. Absence is reported too, on the same reasoning as
the steps above.

## Running it

    python3 lint-configs/check-drift.py            # report, exit 1 on drift
    python3 lint-configs/check-drift.py --repo gog # one repository

It reads each file through `gh api`, so it needs `gh` authenticated and clones
nothing.
