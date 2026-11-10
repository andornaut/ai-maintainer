# AI Maintainer

[![Release](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml/badge.svg)](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/MIT)

Automated GitHub repository maintenance with AI-powered dependency updates, test fixing, and CI monitoring.

## Requirements

| | |
| --- | --- |
| Python | 3.12 or newer |
| Tools | Git, GNU Make, [GitHub CLI](https://cli.github.com/), and each project's container tooling |
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
| 5 | Run the full project verification gate, and ask the agent to fix failures |
| 6 | Commit what the agent changed, and push |
| 7 | Wait for CI, and fix a build this run broke |

### Skipped repositories

A repository is skipped, not failed, when it is ai-maintainer itself, named in `--exclude`, not a git repository, not on its default branch (or the branch cannot be determined), has a dirty working tree before or after the pull, cannot be pulled, or is not writable because it is archived or read-only.

A repository with no dependency files remains eligible. It must declare the verification interface below; its existing CI is still checked.

### Project execution contract

Each maintained repository exposes `make check`, which runs its complete verification gate in its project image. This is the only Make target the consumer requires. Native npm, Cargo, Rake and other task-runner commands remain host commands and do not delegate to Make. Native hooks retain their ordinary behavior, including staged-file autofixes.

Before pulling and before dependency work, the consumer requires a root `Makefile` and an available Make executable. It does not probe targets, read verification metadata, detect languages or infer an alternative command. A missing `check` target is reported when `make check` runs.

The project's thin Make orchestration is trusted. Its launcher owns runtime versions, dependency setup, container isolation and cleanup. Writable execution creates the per-worktree Git metadata marker `container-exec-active` before launching its container and removes it only after confirmed teardown. The consumer checks that marker around managed commands and agent calls. Unconfirmed or unreadable state preserves source changes and stashes, and prevents subsequent resets or commits; the consumer never manages Docker.

Dependency queries, installs and updates use native project tools on the host. Git retains its existing host authentication and fires the project's actual hooks. The AI agent is instructed to verify with `make check` and preserve lint, test and build configuration. This instruction does not sandbox the agent's host process. Dry runs do not pull, run push-probe hooks, update dependencies or invoke the verification gate.

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

### Merging dependabot PRs

Step 3 squash-merges each verified PR with a body built from the PR description, this tool's attribution and the PR's `Co-authored-by` trailers, so the release notes and credits stay in git history and a later run can recognize the merge as its own.

Emdashes and endashes in that body are rewritten as hyphens, dashes inside a URL excepted. The body becomes a commit message the moment the PR is squashed, a quoted changelog routinely carries those characters, and a repository whose CI scans commit messages for them would then fail on a commit that no change to the working tree can fix.

### Pushing

Step 6 pushes any unpushed commit on the branch, not only its own, so a dependabot merge pulled in earlier always reaches the remote. A push carrying only commits that were already there is not counted as a change the run made and does not name the repository in the summary, though CI is still watched on it, because a push triggers CI whoever wrote the commits.

### Verification

Step 5 invokes `make check` once after dependency changes or merges. The project determines which checks its full gate runs and their order. The gate receives the sum of `--lint-timeout` and `--test-timeout` once, rather than separate invocations that repeat its work.

- Exit zero means project verification passed. It does not establish that a test suite exists.
- A nonzero gate, including a missing `check` target, is a verification failure with its command output. No native command or AI inference substitutes for the gate.
- A missing Makefile or unavailable Make executable blocks verification and committing. Disabling verification with `--no-run-tests` reports changes as unverified.
- Dependency updates and both fix paths reject a missing verification entrypoint. The agent is instructed not to remove or weaken checks; the consumer does not infer check categories.
- Before and after the gate, the maintainer fingerprints HEAD, the index, working-tree status and changed-file contents. A changed or unreadable snapshot discards the result and preserves source changes and any pending stash. Credential-shaped paths and changed paths resolving outside the checkout are refused.
- Commands run in bounded process groups. On timeout, the project launcher gets a termination signal and time to clean up before forced termination. Container lifecycle remains the project's responsibility.
- `--repo-timeout` bounds each repository's total wall clock, CI waits included.

### CI verdicts

Verdicts come from the commit's check runs (`/commits/{sha}/check-runs`), which covers every job attached to the commit whatever triggered it, including checks reported through the Checks API by apps other than Actions, and workflows that failed to load before running a job. Checks from any workflow on the commit count, scheduled ones included, so a nightly job failing on the branch head during a run is attributed to that run.

Classic commit statuses (Travis, Netlify and other older external reporters) are a separate, older mechanism and are not consulted.

A failing verdict is returned at once. A non-failing one is held back until the set of checks has been unchanged for two 30-second polls, counted from when the verdict first became terminal. That costs one extra poll on the passing path and covers both GitHub's check-creation window and a workflow chained off another's completion. A check still not created by the end of that window cannot be observed.

### Fixing a CI failure

Every failed run on the commit is asked for its failing jobs' logs, and the first that answers is what the agent is given. One push commonly fails more than one workflow, and the run reporting a failure need not hold its log: a job run by a called reusable workflow is reported on the calling run while its log belongs to its own.

Where `gh run view --log-failed` prints nothing for every run, each failing job's log is read from `repos/{owner}/{repo}/actions/jobs/{id}/logs` instead. gh builds its output from the per-step files in the run's log archive, and GitHub can serve an archive holding only each job's combined log.

When no run yields a failing job's log, a full run log is passed instead, prefixed with a note that says so and names the commit's failing checks. A run whose own jobs all passed logs nothing but successful output, and offered as a failure log it sends the agent looking for an error that is not in the text.

Each attempt names the run whose log the agent is reading. A commit that fails several workflows has more than one failed run, so the URL the failure was reported under is only the newest of them and need not be the one that answered.

`--max-fix-attempts` bounds the retries, but an agent that reads the failure and declines to fix it ends them immediately. The tree is reset and the logs come from the same commit, so a further attempt asks the identical question and spends the agent timeout on the identical answer. A fix that was made and did not hold is retried as normal.

## Options

| Flag | Description |
| --- | --- |
| `-d`, `--base-dir DIR` | Directory to scan for repositories (default: current directory) |
| `-n`, `--dry-run` | Preview without making changes |
| `--limit N` | Stop after N repos have been changed, successfully or not |
| `-e`, `--exclude REPO` | Exclude a repository by name (repeatable) |
| `--no-merge-dependabot` | Skip merging dependabot PRs |
| `--no-update-dependencies` | Skip dependency updates |
| `--no-run-tests` | Skip the full `make check` gate |
| `--no-push` | Commit locally, do not push |
| `--dependency-min-age-days N` | Skip dependencies newer than N days (default: 0) |
| `--max-fix-attempts N` | Max agent fix attempts per repo (default: 4) |
| `--agent-command CMD` | AI agent executable (default: `claude`) |
| `--agent-flags FLAGS` | Flags passed to the agent (default: `--dangerously-skip-permissions`) |
| `--agent-timeout N` | Seconds per agent invocation (default: 300) |
| `--lint-timeout N` | Seconds added to the aggregate gate budget (default: 180) |
| `--test-timeout N` | Seconds added to the gate budget, and the budget for hook-firing Git operations (default: 600) |
| `--command-timeout N` | Seconds for git, gh and other shell commands (default: 120) |
| `--ci-timeout N` | Minutes to wait for CI (default: 10) |
| `--repo-timeout N` | Minutes per repository, 0 for no limit (default: 60) |
| `-v`, `--verbose` | Debug output |
| `-q`, `--quiet` | Warnings, errors and the end-of-run summary only |

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
| Unverified commits | A passing project gate does not establish that its checks include a test suite. |

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
make check
```

The project-owned launcher runs compilation, the full pytest suite, Ruff lint and formatting in an isolated container.

## License

MIT
