# AI Agent Integration

AI Maintainer uses AI agents for:

- **Dependency updates**: Analyze and update direct dependencies
- **Test fixing**: Diagnose and fix test failures after updates
- **CI fixing**: Analyze CI logs and fix build failures

Dependabot PR verification is programmatic (GPG signature + branch pattern) - no AI involved.

## Agent Protocol

Agents receive a prompt as CLI argument, return JSON to stdout, exit 0 on success.

**Request**: Repository name/path, task description, context data, security warnings, expected JSON schema.

**Response schemas**:

```json
// Dependency updates
{"updated": true, "changes_made": "description", "reasoning": "why"}

// Test/CI fixes
{"fixed": true, "changes_made": "description", "reasoning": "why"}
```

## Supported Agents

| Agent                 | Command                                             |
| --------------------- | --------------------------------------------------- |
| Claude Code (default) | `--agent-command claude`                            |
| Ollama                | `--agent-command ollama --agent-flags "run llama3"` |
| Custom                | Any executable: prompt in, JSON out, exit 0         |

## Timeouts

- Agent execution: 300s (5 min)
- CI polling: 600s (10 min)

## Security

AI agents receive untrusted data (CI logs, dependency files, test output) that could contain prompt injection.

**Mitigations**:

- Security warnings in every prompt
- JSON schema enforcement
- No AI for dependabot (GPG only)
- Log truncation (10KB max)

**Known risks**:

- AI commands executed with `shell=True`
- No package verification (relies on age threshold)
- CI logs may leak secrets to AI provider

**Before production**: `--dry-run` first, `--limit 1`, exclude critical repos, set high `--dependency-min-age-days`.

## Troubleshooting

| Issue            | Fix                                                  |
| ---------------- | ---------------------------------------------------- |
| Agent timeout    | Ensure non-interactive mode, check network           |
| Invalid JSON     | Use `--verbose`, test agent manually                 |
| No updates       | Check `--dependency-min-age-days`, verify deps exist |
| Tests still fail | Check `--max-fix-attempts`, review AI reasoning      |
