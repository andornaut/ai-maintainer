# GitHub Maintainer

Automated GitHub repository maintenance with AI-powered decision making.

## Features

- Pulls latest changes from default branch
- Merges dependabot PRs (verified by GPG signature)
- Updates dependencies via AI analysis (respects age threshold)
- Runs tests and asks AI to fix failures
- Commits and pushes if tests pass
- Optionally waits for CI and fixes remote failures

## Installation

```bash
git clone https://github.com/andornaut/github-maintainer.git
cd github-maintainer
chmod +x github-maintainer

# Add to PATH
ln -s "$(pwd)/github-maintainer" ~/.local/bin/
```

**Requirements**: Python 3.7+, Git, GitHub CLI (`gh`), [Claude Code CLI](https://docs.anthropic.com/en/docs/claude-code) or [Ollama](https://ollama.ai/)

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
```

## Usage

```bash
github-maintainer                               # Current directory
github-maintainer --base-dir ~/src/github.com   # Specify directory
github-maintainer --dry-run --verbose           # Preview changes
github-maintainer --wait-for-ci                 # Wait for CI, fix failures
github-maintainer -e repo1 -e repo2             # Exclude repos
```

**Options**:

| Flag | Description |
|------|-------------|
| `--dry-run` | Preview without making changes |
| `--limit N` | Process at most N repos with changes |
| `--no-merge-dependabot` | Skip merging dependabot PRs |
| `--no-update-dependencies` | Skip dependency updates |
| `--no-run-tests` | Skip running tests |
| `--no-push` | Don't push to remote |
| `--wait-for-ci` | Wait for CI and fix failures |
| `--dependency-age-threshold N` | Skip updates newer than N days (default: 30) |
| `--agent-command CMD` | AI agent executable (default: `claude`) |
| `-e/--exclude REPO` | Exclude repository by name (repeatable) |

## Automation

```bash
# Cron (daily at 3 AM)
0 3 * * * github-maintainer --base-dir ~/src/github.com 2>&1
```

## Security

AI agents receive untrusted data (CI logs, dependency files) that could contain prompt injection attacks. Mitigations are implemented but use caution:

- Always use `--dry-run` first
- Only run on repos where you control PR creation
- Review changes before pushing

See [AGENTS.md](AGENTS.md) for agent configuration and security details.

## License

MIT
