# AI Maintainer

[![Release](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml/badge.svg)](https://github.com/andornaut/ai-maintainer/actions/workflows/release.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/license/MIT)

Automated GitHub repository maintenance with AI-powered dependency updates, test fixing, and CI monitoring.

## Requirements

| Requirement | Details |
| --- | --- |
| Python | 3.12 or newer |
| Tools | Git, GNU Make, [GitHub CLI](https://cli.github.com/), and each project's container tooling |
| AI agent | [Claude Code CLI](https://code.claude.com/docs), or any command that reads a prompt on argv (e.g. [Ollama](https://ollama.com/)) |

The GitHub CLI must be authenticated. ai-maintainer checks authentication at startup and exits if it fails. CI status, write-access detection and dependabot verification use the GitHub API.

For GitHub CLI's OAuth authentication, grant both `repo` and `workflow` scopes. Without `workflow`, merging a dependabot PR that updates a GitHub Action can fail with `refusing to allow an OAuth App to create or update workflow ... without 'workflow' scope`.

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
ai-maintainer                                # repositories directly inside the current directory
ai-maintainer --base-dir ~/src --limit 5       # elsewhere, stop after 5 changed
ai-maintainer -e forked-repo -e broken-repo    # skip named repos
ai-maintainer --agent-command "ollama run llama3" --agent-flags ""
```

Custom agents receive the complete prompt as their final command-line argument
and must return a JSON object on stdout with the fields requested by the prompt.
Use `--agent-flags ""` to clear the Claude-specific default flags, or supply flags
supported by the custom agent.

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
| 7 | Monitor CI when the baseline commit had checks or its status could not be read, and try to fix failures from this run |

### Skipped repositories

A repository is skipped, not failed, when it is ai-maintainer itself, named in `--exclude`, not a git repository, not on its default branch (or the branch cannot be determined), has a dirty working tree before or after the pull, cannot be pulled, or is not writable because it is archived or read-only.

A repository with no dependency files remains eligible. It must declare the verification interface below; its existing CI is still checked.

### Project execution contract

Each maintained repository exposes `make check`, which runs its complete verification gate in its project image. This is the only Make target the consumer requires. Native npm, Cargo, Rake and other task-runner commands remain host commands and do not delegate to Make. Native hooks retain their ordinary behavior, including staged-file autofixes.

Before pulling and before dependency work, ai-maintainer checks that a root `Makefile` is a file and that `make` is on `PATH`. It does not check the Make version, probe targets or infer an alternative command. A missing `check` target is reported when `make check` runs.

The project's Make targets own runtime versions, dependency setup, container isolation and cleanup. The consumer invokes them without managing Docker.

Dependency queries, installs and updates use native project tools on the host. Git uses host authentication and the project's hooks. The AI agent is instructed to verify with `make check` and preserve lint, test and build configuration. This instruction does not sandbox the agent's host process.

Dry runs still require authentication, a clean default branch, a root `Makefile` and `make` on `PATH`. They read CI and dependabot PRs, but do not pull, probe push access, merge PRs, invoke the agent, update dependencies or run the verification gate.

### Dependency updates

Step 4 asks the package managers what is out of date and hands the agent that list, so successive runs over an unchanged repository agree on what is available.

| Manifest | Asked | Dated against |
| --- | --- | --- |
| `Gemfile` | `bundle outdated --parseable` | RubyGems |
| `package.json` | `npm outdated --json` | npm |
| `requirements.txt`, `requirements-dev.txt` | its `==` pins | PyPI |
| Everything else | nothing | nothing |

For pip requirements, ai-maintainer reads the pins directly rather than querying the installed environment with `pip list --outdated`. Its PyPI lookup excludes pre-release and development versions. Ruby and npm candidates come from their package managers' reports.

Anything published within `--dependency-min-age-days` is dropped before the list reaches the agent. **That is the only place the tool applies the limit.** Three cases reach the agent unfiltered:

- A manifest with no query. Cargo, Go, `Pipfile` and `pyproject.toml` get no list and no date check.
- A candidate whose release date could not be read. It is passed on rather than dropped, because not knowing its age is not knowing it is too new.
- A report the tool cannot parse, such as a `bundle outdated` that errored. It is forwarded whole, with no dates looked up.

In all three the limit is stated to the agent and the agent decides. Treat it as enforced for Ruby, npm and pinned pip requirements, and advisory everywhere else. The prompt names which manifests were answered for. Parsed entries carry a release date or an explicit unknown-date note, so the agent can distinguish filtered candidates from undated entries.

### Merging dependabot PRs

Step 3 squash-merges each verified PR with a body built from the PR description, this tool's attribution and the PR's `Co-authored-by` trailers. This preserves release notes and credits and lets a later run recognize the merge as its own. On branches requiring a merge queue, GitHub CLI ignores the supplied merge body, so the tool cannot recognize that merge by its attribution.

Em dashes and en dashes in the body are rewritten as hyphens, except inside URLs. This keeps quoted changelogs from breaking CI rules that scan commit messages.

### Pushing

Step 6 pushes all unpushed commits on the branch, including commits that predate the run. Those commits can trigger CI but do not count toward the run's change limit.

### Verification

Step 5 invokes `make --no-print-directory check` once after dependency changes or merges. The project determines its checks and their order. The command's timeout is the sum of `--lint-timeout` and `--test-timeout`.

- Exit zero means project verification passed. It does not establish that a test suite exists.
- A nonzero gate, including a missing `check` target, is a verification failure with its command output. No native command or AI inference substitutes for the gate.
- A missing Makefile or unavailable Make executable blocks verification and committing. Disabling verification with `--no-run-tests` reports changes as unverified.
- Dependency updates and both fix paths reject a missing verification entrypoint. The agent is instructed not to remove or weaken checks; the consumer does not infer check categories.
- Commands run in bounded process groups. On timeout, the project launcher gets a termination signal and time to clean up before forced termination. Container lifecycle remains the project's responsibility.
- `--repo-timeout` is checked between fix attempts and caps CI waits to the remaining whole minutes. It does not interrupt an active command or agent invocation, so a repository can exceed this budget.

### CI verdicts

Verdicts come from the commit's check runs (`/commits/{sha}/check-runs`), which covers every job attached to the commit whatever triggered it, including checks reported through the Checks API by apps other than Actions, and workflows that failed to load before running a job. Checks from any workflow on the commit count, scheduled ones included, so a nightly job failing on the branch head during a run is attributed to that run.

Classic commit statuses (Travis, Netlify and other older external reporters) are a separate, older mechanism and are not consulted.

While monitoring, ai-maintainer returns failure immediately. For other terminal results, it normally waits for two polls with the same set of checks, 30 seconds apart, to give delayed checks time to appear. If the wait expires first, it uses the last terminal result it saw, if any.

### Fixing a CI failure

ai-maintainer requests failing-job logs from each failed run on the commit until one returns them. A reusable workflow's failing job can be reported on its caller while its log belongs to another run.

Where `gh run view --log-failed` prints nothing for every run, each failing job's log is read from `repos/{owner}/{repo}/actions/jobs/{id}/logs` instead. gh builds its output from the per-step files in the run's log archive, and GitHub can serve an archive holding only each job's combined log.

If no failing-job log is available, ai-maintainer tries the newest failed run's full log. It labels the fallback and includes any failing check names it can read. When GitHub could not load a workflow, ai-maintainer can report the startup failure without a job log.

Each attempt names the run that supplied its log. This may differ from the newest failed run linked in the failure report.

`--max-fix-attempts` limits retries. An agent that declines to fix the failure ends retries immediately. An attempted fix that fails verification can be retried.

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
| `--repo-timeout N` | Minutes budgeted per repository, checked between fix attempts and used to cap CI waits (default: 60, 0 for no limit) |
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
- The agent runs in its own process group with no terminal or stdin and a timeout. A repository budget limits further fix attempts and CI waits.
- If the agent reports no dependency updates but modified files, those changes are discarded. A claimed fix that leaves no changes is rejected.
- The pre-commit log names up to 20 staged paths and reports how many more were omitted.
- Nothing already pushed is ever rewritten, and nothing is ever force pushed.
- Blocked verification prevents committing. Explicitly disabling verification logs the unverified changes at info level.

### Why nothing pushed is ever undone

When a build breaks and the agent cannot fix it, ai-maintainer reports the failure and stops. It does not rewind the branch.

Rewinding would require a force push and could remove commits made by others. For a squash-merged dependabot PR, GitHub would still record the PR as merged even though its update was removed. Leaving the failed build visible preserves the update for a deliberate fix or revert.

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

The Make gate runs Ruff lint and formatting, the full pytest suite and Python byte-compilation in the declared Python stock image. Markdown and ShellCheck use separate pinned images. See [container tooling](tests/container/README.md) for individual checks, focused tests and arbitrary commands.

## License

MIT
