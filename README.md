# GitHub Maintainer

Automated GitHub repository maintenance with AI-powered dependency updates, test fixing, and CI monitoring.

Merges dependabot PRs (GPG-verified), updates direct dependencies (with age constraints), fixes test failures, and monitors CI builds across multiple repos. Supports Python (pipenv/poetry), Node.js (nvm/fnm), Ruby (chruby/rbenv), Rust, and Go.

## Installation

```bash
git clone https://github.com/andornaut/github-maintainer.git
cd github-maintainer
chmod +x github-maintainer
ln -s "$(pwd)/github-maintainer" ~/.local/bin/
```

**Requirements**: Python 3.7+, Git, [GitHub CLI](https://cli.github.com/) (`gh auth login`), and [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) (or [Ollama](https://ollama.ai/))

## Usage

```bash
# Preview changes (recommended first run)
github-maintainer --dry-run --verbose

# Process all repos in current directory
github-maintainer

# Process specific directory, limit to 5 repos
github-maintainer --base-dir ~/src/github.com --limit 5

# Exclude specific repos
github-maintainer -e forked-repo -e broken-repo

# Use different AI agent
github-maintainer --agent-command "ollama run llama3"
```

## How it works

For each repository:

1. Pull latest changes
2. Merge dependabot PRs (GPG-verified, branch pattern checked)
3. Ask AI to analyze and update direct dependencies (skips new packages < 30 days old)
4. Run tests, ask AI to fix failures (with retries)
5. Commit and push changes
6. Wait for CI and fix build failures (automatic if CI exists)

Skips repos that are: not git repos, not on default branch, have uncommitted changes, are archived/read-only, or have no dependency files.

## Options

| Flag                          | Description                                                           |
| ----------------------------- | --------------------------------------------------------------------- |
| `--dry-run`, `-n`             | Preview without making changes                                        |
| `--limit N`                   | Process at most N repos with changes                                  |
| `--no-merge-dependabot`       | Skip merging dependabot PRs                                           |
| `--no-update-dependencies`    | Skip dependency updates                                               |
| `--no-run-tests`              | Skip running tests                                                    |
| `--no-push`                   | Don't push to remote (local commits only)                             |
| `--rollback-on-ci-failure`    | Automatically rollback (force push) if CI fails after push            |
| `--dependency-min-age-days N` | Skip dependencies newer than N days (default: 30)                     |
| `--max-fix-attempts N`        | Max AI fix retry attempts per repo (default: 4)                       |
| `--agent-command CMD`         | AI agent executable (default: `claude`)                               |
| `--agent-flags FLAGS`         | Flags passed to agent (default: `--dangerously-skip-permissions`)     |
| `-e/--exclude REPO`           | Exclude repository by name (repeatable)                               |
| `-v/--verbose`                | Verbose output (debug level)                                          |
| `-q/--quiet`                  | Errors only                                                           |

## Security

AI agents receive untrusted data (CI logs, dependency files, test output) that could contain prompt injection attacks. See [AGENTS.md](AGENTS.md) for detailed security analysis.

**Risks**: AI-generated commands are executed with `shell=True`. No verification that package updates are legitimate. CI logs (up to 10KB) sent to AI provider may leak secrets.

**Mitigations**: Security warnings in prompts, JSON schema enforcement, GPG signature checking for dependabot (no AI), process isolation with timeouts.

**Before running in production**: Use `--dry-run` first, start with `--limit 1`, exclude critical repos with `-e`, set `--dependency-min-age-days` high (90+) for sensitive projects, understand that AI commands run with your user privileges.

## Developing

```bash
# Run tests
python -m pytest test_github_maintainer.py -v

# Run a single test
python -m pytest test_github_maintainer.py::TestValidateCommand -v
```

## License

MIT
