# AI Maintainer

[![Release](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml/badge.svg)](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/MIT)

Automated GitHub repository maintenance with AI-powered dependency updates, test fixing, and CI monitoring.

## Requirements

| | |
| --- | --- |
| Python | 3.12 or newer |
| Tools | Git, [GitHub CLI](https://cli.github.com/) |
| AI agent | [Claude Code CLI](https://code.claude.com/docs), or any command that reads a prompt on argv (e.g. [Ollama](https://ollama.com/)) |

The GitHub CLI must be authenticated. ai-maintainer checks once at startup and exits if the check fails, because CI status, write-access detection and dependabot verification all go through the API, and a run without it would report success for work it never did.

The token needs the `repo` **and** `workflow` scopes. Without `workflow`, merging a dependabot PR that bumps a GitHub Action fails with `refusing to allow an OAuth App to create or update workflow ... without 'workflow' scope`.

```bash
gh auth login
gh auth refresh -h github.com -s workflow
```

## Installation

```bash
git clone https://github.com/andornaut/ai-maintainer.git
cd ai-maintainer
chmod +x ai-maintainer
ln -s "$(pwd)/ai-maintainer" ~/.local/bin/
```

## Usage

```bash
ai-maintainer --dry-run --verbose              # preview, recommended first run
ai-maintainer                                  # every repo below the current directory
ai-maintainer --base-dir ~/src --limit 5       # elsewhere, stop after 5 changed
ai-maintainer -e forked-repo -e broken-repo    # skip named repos
ai-maintainer --agent-command "ollama run llama3"
```

## How it works

Each repository is taken through these steps in order:

| # | Step |
| --- | --- |
| 1 | Validate: a git repo, on its default branch, clean tree, writable |
| 2 | Check CI, and fix a failure left by an earlier run |
| 3 | Merge dependabot PRs on GitHub, then pull |
| 4 | Ask the agent to update direct dependencies |
| 5 | Run the tests, and ask the agent to fix failures |
| 6 | Commit what the agent changed, and push |
| 7 | Wait for CI, and fix a build this run broke |

### Skipped repositories

A repository is skipped, not failed, when it is ai-maintainer itself, named in `--exclude`, not a git repository, not on its default branch (or the branch cannot be determined), has a dirty working tree before or after the pull, cannot be pulled, or is not writable because it is archived or read-only.

A repository with no dependency files is not skipped. Its tests still run and its CI is still watched.

### Toolchain activation

Each project's toolchain is detected and activated before tests and git operations, so hooks such as husky `pre-commit` use the project's runtime rather than the ambient PATH. The same activation prefix is given to the agent, so its package-manager commands resolve the same runtime.

| Language | Detected from | Activated with |
| --- | --- | --- |
| Node | `.nvmrc` | nvm, fnm |
| Python | `Pipfile`, `poetry.lock`, a venv directory | pipenv, poetry, venv |
| Ruby | `Gemfile`, `.ruby-version` | chruby, rbenv |
| Rust | `Cargo.toml` | cargo |
| Go | `go.mod` | the Go toolchain |

### Dependency updates

Step 4 asks the package managers what is out of date and hands the agent that list, so successive runs over an unchanged repository agree on what is available.

| Manifest | Asked | Dated against |
| --- | --- | --- |
| `Gemfile` | `bundle outdated --parseable` | RubyGems |
| `package.json` | `npm outdated --json` | npm |
| `requirements.txt`, `requirements-dev.txt` | its `==` pins | PyPI |
| Everything else | nothing | nothing |

pip has no query for a pinned file: `pip list --outdated` reports the environment that happens to be installed, not what the file pins, so the pins are read directly. Pre-releases are never offered.

Anything published within `--dependency-min-age-days` is dropped before the list reaches the agent. **That is the only place the tool applies the limit.** Three cases reach the agent unfiltered:

- A manifest with no query. Cargo, Go, `Pipfile` and `pyproject.toml` get no list and no date check.
- A candidate whose release date could not be read. It is passed on rather than dropped, because not knowing its age is not knowing it is too new.
- A report the tool cannot parse, such as a `bundle outdated` that errored. It is forwarded whole, with no dates looked up.

In all three the limit is stated to the agent and the agent decides. Treat it as enforced for Ruby, npm and pinned pip requirements, and advisory everywhere else. The prompt names which manifests were answered for and every entry carries its release date, so the agent can tell the filtered from the unfiltered.

### Pushing

Step 6 pushes any unpushed commit on the branch, not only its own, so a dependabot merge pulled in earlier always reaches the remote. A push carrying only commits that were already there is not counted as a change the run made and does not name the repository in the summary, though CI is still watched on it, because a push triggers CI whoever wrote the commits.

### Verification

- A repository with no detectable test command is reported as unverified, never as passing.
- A repository whose declared test runner will not start is abandoned rather than committed to.
- `--repo-timeout` bounds each repository's total wall clock, CI waits included, so one repository cannot stall the rest of the run.

### CI verdicts

Verdicts come from the commit's check runs (`/commits/{sha}/check-runs`), which covers every job attached to the commit whatever triggered it, including checks reported through the Checks API by apps other than Actions, and workflows that failed to load before running a job. Checks from any workflow on the commit count, scheduled ones included, so a nightly job failing on the branch head during a run is attributed to that run.

Classic commit statuses (Travis, Netlify and other older external reporters) are a separate, older mechanism and are not consulted.

A failing verdict is returned at once. A non-failing one is held back until the set of checks has been unchanged for two 30-second polls, counted from when the verdict first became terminal. That costs one extra poll on the passing path and covers both GitHub's check-creation window and a workflow chained off another's completion. A check still not created by the end of that window cannot be observed.

## Options

| Flag | Description |
| --- | --- |
| `-d`, `--base-dir DIR` | Directory to scan for repositories (default: current directory) |
| `-n`, `--dry-run` | Preview without making changes |
| `--limit N` | Stop after N repos have been changed, successfully or not |
| `-e`, `--exclude REPO` | Exclude a repository by name (repeatable) |
| `--no-merge-dependabot` | Skip merging dependabot PRs |
| `--no-update-dependencies` | Skip dependency updates |
| `--no-run-tests` | Skip running tests |
| `--no-push` | Commit locally, do not push |
| `--dependency-min-age-days N` | Skip dependencies newer than N days (default: 0) |
| `--max-fix-attempts N` | Max agent fix attempts per repo (default: 4) |
| `--agent-command CMD` | AI agent executable (default: `claude`) |
| `--agent-flags FLAGS` | Flags passed to the agent (default: `--dangerously-skip-permissions`) |
| `--agent-timeout N` | Seconds per agent invocation (default: 300) |
| `--test-timeout N` | Seconds for the test suite and hook-firing git ops (default: 600) |
| `--command-timeout N` | Seconds for git, gh and other shell commands (default: 120) |
| `--ci-timeout N` | Minutes to wait for CI (default: 10) |
| `--repo-timeout N` | Minutes per repository, 0 for no limit (default: 60) |
| `-v`, `--verbose` | Debug output |
| `-q`, `--quiet` | Warnings and errors only |

## Security

ai-maintainer runs an AI agent with `--dangerously-skip-permissions` over repositories it then commits and pushes to. Both halves of that carry risk.

### Threat model

The agent is fed untrusted data on every run: dependabot PR titles and bodies, branch names, dependency files, test output, and CI logs. Any of it can carry prompt injection. The agent also runs package-manager commands, which execute third-party install scripts (`postinstall`, `build.rs`, gem extensions) with your user privileges.

What is trusted: the GitHub CLI's authentication, dependabot's identity as reported by GitHub, and the repositories' own git hooks.

### Risks

| Risk | Detail |
| --- | --- |
| Agent privileges | Shell commands run as your user with no sandbox. A successful injection, or a malicious `postinstall`, has your full access. |
| Unverified package contents | Updates are checked for age, not legitimacy. A compromised upstream release is merged like any other. |
| Data sent to the AI provider | CI logs (up to 20 KB), test output and dependency files leave your machine. CI logs can contain secrets a workflow echoed. |
| Red default branch | An unfixable CI failure is left in place, deliberately (see below), so an unattended run can leave the branch broken. |
| Unverified commits | A repository with no detectable test command is committed to on the agent's own report. |

### Mitigations

- Untrusted context is fenced off in the prompt behind an explicit injection warning, and is labelled as data to analyze rather than instructions to follow.
- Dependabot PRs are verified programmatically, with no AI in the loop: the branch must match `dependabot/<ecosystem>/<package>`, the head commit must be signed by GitHub and committed by it, which a rebase under another identity is not, and the merge is pinned to that exact commit with `--match-head-commit`, so a branch that moves between verification and merge is rejected.
- The agent runs in its own process group with no terminal, no stdin, and a hard timeout; each repository additionally has a wall-clock budget.
- The agent's claims are checked against the working tree: changes it did not report are discarded, and a fix it claims to have made but did not is rejected.
- Every file going into a commit is named in the log.
- Nothing already pushed is ever rewritten, and nothing is ever force pushed.
- A repository whose changes could not be verified by a test run says so at warning level.

### Why nothing pushed is ever undone

When a build breaks and the agent cannot fix it, ai-maintainer reports the failure and stops. It does not rewind the branch.

Rewinding would mean force pushing away commits that are already on the remote, and for a squash-merged dependabot PR that loses data: GitHub still records the PR as merged, so dependabot will not re-propose an update it believes already landed. The result is a silently dropped dependency update in exchange for a red build, which is loud, visible and recoverable by anyone. A force push is also the one operation here that another person cannot undo from their own clone, and branch protection blocks it on exactly the repositories where care matters most.

### Before running in production

- Use `--dry-run` first, then `--limit 1`.
- Exclude critical repositories with `-e`.
- Set `--dependency-min-age-days` high (90+) for sensitive projects, remembering it binds only Ruby, npm and pinned pip requirements.
- Watch for repositories reported as failed. Their builds stay broken until you act.
- Run under an account holding no credentials beyond what maintenance needs.

## Developing

```bash
python -m pytest test_ai_maintainer.py -v                 # the suite
python -m pytest test_ai_maintainer.py::TestGitClient -v  # one class
ruff check . && ruff format --check .
```

## License

MIT
