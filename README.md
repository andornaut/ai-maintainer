# AI Maintainer

[![CI](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml/badge.svg)](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

Automated GitHub repository maintenance with AI-powered dependency updates, test fixing, and CI monitoring.

Merges dependabot PRs (GPG-verified), updates direct dependencies (with age constraints), fixes test failures, and monitors CI builds across multiple repos. Supports Python (pipenv/poetry), Node.js (nvm/fnm), Ruby (chruby/rbenv), Rust, and Go.

## Installation

```bash
git clone https://github.com/andornaut/ai-maintainer.git
cd ai-maintainer
chmod +x ai-maintainer
ln -s "$(pwd)/ai-maintainer" ~/.local/bin/
```

**Requirements**: Python 3.9+, Git, [GitHub CLI](https://cli.github.com/), and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (or [Ollama](https://ollama.ai/))

Authenticate the GitHub CLI with `gh auth login`. This is mandatory: ai-maintainer checks it once at startup and exits if it fails, because CI status, write-access detection and dependabot verification all go through the API, and a run without it would report success for work it never did. The token needs the `repo` **and** `workflow` scopes. `workflow` is required to merge dependabot PRs that bump GitHub Actions; without it those merges fail with `refusing to allow an OAuth App to create or update workflow ... without 'workflow' scope`. Add the scope to an existing login with:

```bash
gh auth refresh -h github.com -s workflow
```

## Usage

```bash
# Preview changes (recommended first run)
ai-maintainer --dry-run --verbose

# Process all repos in current directory
ai-maintainer

# Process specific directory, limit to 5 repos
ai-maintainer --base-dir ~/src/github.com --limit 5

# Exclude specific repos
ai-maintainer -e forked-repo -e broken-repo

# Use different AI agent
ai-maintainer --agent-command "ollama run llama3"
```

## How it works

For each repository:

1. Validate repo (git repo, default branch, clean working dir, writable)
2. Check and fix pre-existing CI failures
3. Merge dependabot PRs on GitHub and pull changes (GPG-verified, branch pattern checked, merge pinned to the verified commit)
4. Ask AI to analyze and update direct dependencies (respects `--dependency-min-age-days`)
5. Run tests, ask AI to fix failures (with retries)
6. Commit and push changes
7. Wait for CI and fix build failures (automatic if CI exists); if a failure cannot be fixed, the repository is reported failed and left as it is

ai-maintainer auto-detects each project's toolchain (Node via nvm/fnm, Python via pipenv/poetry/venv, Ruby via chruby/rbenv, plus Go and Rust) and activates it when running tests and git operations, so git hooks (e.g. husky `pre-commit`) use the project's runtime rather than whatever is on the ambient PATH. The same activation prefix is given to the AI agent, so its package-manager commands resolve the project's runtime too.

Skips repos that are: not git repos, not on default branch, have uncommitted changes, are archived/read-only, or have no dependency files.

A repository with no detectable test command is reported as unverified rather than passing, and one whose declared test runner is not installed is abandoned rather than committed to. Each repository has a wall-clock budget (`--repo-timeout`) that bounds its CI waits as well as its fix attempts, so one repo cannot stall the rest of the run.

CI status is read from the commit's checks (`/commits/{sha}/check-runs`), so it covers every job attached to the commit regardless of which event triggered it, including checks reported through the Checks API by apps other than Actions and workflows that failed to load before running a job. Classic commit statuses (Travis, Netlify and other older external reporters) use a separate, older mechanism and are not consulted. A non-failing verdict is held back until the set of checks has been unchanged for two polls counted from when the verdict first became terminal, which costs one extra 30s poll on the passing path; a failing one is returned at once. This covers both GitHub's check-creation window and a workflow chained off another one's completion. A check that has still not been created by the end of that window cannot be observed. Checks from any workflow on the commit count, scheduled ones included, so a nightly job failing on the branch head during a run is attributed to that run.

## Options

| Flag                          | Description                                                           |
| ----------------------------- | --------------------------------------------------------------------- |
| `--base-dir`, `-d DIR`        | Directory to scan for repositories (default: current directory)       |
| `--dry-run`, `-n`             | Preview without making changes                                        |
| `--limit N`                   | Stop after N repos have been changed, successfully or not             |
| `--no-merge-dependabot`       | Skip merging dependabot PRs                                           |
| `--no-update-dependencies`    | Skip dependency updates                                               |
| `--no-run-tests`              | Skip running tests                                                    |
| `--test-timeout N`            | Timeout in seconds for the test suite and hook-firing git ops (600)   |
| `--no-push`                   | Don't push to remote (local commits only)                             |
| `--ci-timeout N`              | Minutes to wait for CI to complete (default: 10)                      |
| `--dependency-min-age-days N` | Skip dependencies newer than N days (default: 0)                      |
| `--max-fix-attempts N`        | Max AI fix retry attempts per repo (default: 4)                       |
| `--repo-timeout N`            | Minutes to spend per repo, 0 for no limit (default: 60)               |
| `--agent-command CMD`         | AI agent executable (default: `claude`)                               |
| `--agent-flags FLAGS`         | Flags passed to agent (default: `--dangerously-skip-permissions`)     |
| `--agent-timeout N`           | Timeout in seconds per AI agent invocation (default: 300)             |
| `--command-timeout N`         | Timeout in seconds for git/gh and shell commands (default: 120)       |
| `-e/--exclude REPO`           | Exclude repository by name (repeatable)                               |
| `-v/--verbose`                | Verbose output (debug level)                                          |
| `-q/--quiet`                  | Warnings and errors only                                              |

## Security

ai-maintainer runs an AI agent with `--dangerously-skip-permissions` over repositories it then commits and pushes to. Both halves of that carry risk.

### Threat model

The agent is fed untrusted data on every run: dependabot PR titles and bodies, branch names, dependency files, test output, and CI logs. Any of it can carry prompt injection. The agent also runs package-manager commands, which execute third-party install scripts (`postinstall`, `build.rs`, gem extensions) with your user privileges.

What is trusted: the GitHub CLI's authentication, dependabot's identity as reported by GitHub, and the repositories' own git hooks.

### Risks

- **Agent privileges**: the agent executes shell commands as your user, with no sandbox. A successful injection, or a malicious `postinstall` in an updated package, runs with your full access.
- **Unverified package contents**: dependency updates are checked for age, not for legitimacy. A compromised upstream release is merged like any other.
- **Data sent to the AI provider**: CI logs (up to 20 KB), test output, and dependency files leave your machine. CI logs can contain secrets that a workflow echoed.
- **Unfixable CI failures are left in place**: a broken build stays broken until a human acts on it. That is deliberate (see below), but it means an unattended run can leave the default branch red.
- **Unverified commits**: a repository with no detectable test command is committed to on the strength of the agent's own report.

### Mitigations

- Untrusted context is fenced off in the prompt behind an explicit injection warning, and is labelled as data to analyze rather than instructions to follow.
- Dependabot PRs are verified programmatically, with no AI in the loop: the branch must match `dependabot/<ecosystem>/<package>`, the head commit must be GPG-verified by GitHub, and the merge is pinned to that exact commit with `--match-head-commit`, so a branch that moves between verification and merge is rejected.
- The agent runs in its own process group with no terminal, no stdin, and a hard timeout; each repository additionally has a wall-clock budget.
- The agent's claims are checked against the working tree: changes it did not report are discarded, and a fix it claims to have made but did not is rejected.
- Every file going into a commit is named in the log, so anything the agent left behind is visible.
- Nothing already pushed is ever rewritten. ai-maintainer force pushes nowhere, so no history another clone depends on can be destroyed by it.
- A repository whose changes could not be verified by a test run says so at warning level.

### Why nothing pushed is ever undone

When a build breaks and the agent cannot fix it, ai-maintainer reports the failure and stops. It does not rewind the branch.

Rewinding would mean force pushing away commits that are already on the remote, and for a squash-merged dependabot PR that loses data: GitHub still records the PR as merged, so dependabot will not re-propose an update it believes already landed. The result is a silently dropped dependency update in exchange for a red build, which is loud, visible and recoverable by anyone. A force push is also the one operation here that another person cannot undo from their own clone, and branch protection blocks it on exactly the repositories where care matters most.

### Before running in production

Use `--dry-run` first, start with `--limit 1`, exclude critical repos with `-e`, and set `--dependency-min-age-days` high (90+) for sensitive projects. Watch for repositories reported as failed: their builds stay broken until you act. Understand that agent commands run with your user privileges, and prefer running under an account that does not hold credentials beyond what maintenance needs.

## Developing

```bash
# Run tests
python -m pytest test_ai_maintainer.py -v

# Run a single test
python -m pytest test_ai_maintainer.py::TestGitClient -v
```

## License

MIT
