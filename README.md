# AI Maintainer

Automated GitHub repository maintenance with AI-powered dependency updates, test fixing, and CI monitoring.

Merges dependabot PRs (GPG-verified), updates direct dependencies (with age constraints), fixes test failures, and monitors CI builds across multiple repos. Supports Python (pipenv/poetry), Node.js (nvm/fnm), Ruby (chruby/rbenv), Rust, and Go.

## Installation

```bash
git clone https://github.com/andornaut/ai-maintainer.git
cd ai-maintainer
chmod +x ai-maintainer
ln -s "$(pwd)/ai-maintainer" ~/.local/bin/
```

**Requirements**: Python 3.7+, Git, [GitHub CLI](https://cli.github.com/), and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (or [Ollama](https://ollama.ai/))

Authenticate the GitHub CLI with `gh auth login`. The token needs the `repo` **and** `workflow` scopes. `workflow` is required to merge dependabot PRs that bump GitHub Actions; without it those merges fail with `refusing to allow an OAuth App to create or update workflow ... without 'workflow' scope`. Add the scope to an existing login with:

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
3. Merge dependabot PRs on GitHub and pull changes (GPG-verified, branch pattern checked)
4. Ask AI to analyze and update direct dependencies (respects `--dependency-min-age-days`)
5. Run tests, ask AI to fix failures (with retries)
6. Commit and push changes
7. Wait for CI and fix build failures (automatic if CI exists)

ai-maintainer auto-detects each project's toolchain (Node via nvm/fnm, Python via pipenv/poetry/venv, Ruby via chruby/rbenv, plus Go and Rust) and activates it when running tests and git operations, so git hooks (e.g. husky `pre-commit`) use the project's runtime rather than whatever is on the ambient PATH.

Skips repos that are: not git repos, not on default branch, have uncommitted changes, are archived/read-only, or have no dependency files.

## Options

| Flag                          | Description                                                           |
| ----------------------------- | --------------------------------------------------------------------- |
| `--dry-run`, `-n`             | Preview without making changes                                        |
| `--limit N`                   | Process at most N repos with changes                                  |
| `--no-merge-dependabot`       | Skip merging dependabot PRs                                           |
| `--no-update-dependencies`    | Skip dependency updates                                               |
| `--no-run-tests`              | Skip running tests                                                    |
| `--test-timeout N`            | Timeout in seconds for the test suite (default: 600)                  |
| `--no-push`                   | Don't push to remote (local commits only)                             |
| `--rollback-on-ci-failure`    | Automatically rollback (force push) if CI fails after push            |
| `--ci-timeout N`              | Minutes to wait for CI to complete (default: 10)                      |
| `--dependency-min-age-days N` | Skip dependencies newer than N days (default: 0)                      |
| `--max-fix-attempts N`        | Max AI fix retry attempts per repo (default: 4)                       |
| `--agent-command CMD`         | AI agent executable (default: `claude`)                               |
| `--agent-flags FLAGS`         | Flags passed to agent (default: `--dangerously-skip-permissions`)     |
| `--agent-timeout N`           | Timeout in seconds per AI agent invocation (default: 300)             |
| `--command-timeout N`         | Timeout in seconds for git/gh and shell commands (default: 120)       |
| `-e/--exclude REPO`           | Exclude repository by name (repeatable)                               |
| `-v/--verbose`                | Verbose output (debug level)                                          |
| `-q/--quiet`                  | Errors only                                                           |

## Security

AI agents receive untrusted data (CI logs, dependency files, test output) that could contain prompt injection attacks. See [AGENTS.md](AGENTS.md) for detailed security analysis.

**Risks**: The AI agent runs with `--dangerously-skip-permissions`, so it executes package-manager and other shell commands with your user privileges. No verification that package updates are legitimate. CI logs (up to 10KB) sent to AI provider may leak secrets.

**Mitigations**: Security warnings in prompts, JSON schema enforcement, GPG signature checking for dependabot (no AI), process isolation with timeouts.

**Before running in production**: Use `--dry-run` first, start with `--limit 1`, exclude critical repos with `-e`, set `--dependency-min-age-days` high (90+) for sensitive projects, understand that AI commands run with your user privileges.

## Developing

```bash
# Run tests
python -m pytest test_ai_maintainer.py -v

# Run a single test
python -m pytest test_ai_maintainer.py::TestGitClient -v
```

## License

MIT
