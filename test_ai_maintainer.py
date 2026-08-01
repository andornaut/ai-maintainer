#!/usr/bin/env python3
"""Unit tests for ai-maintainer."""

import importlib.machinery

# Import the module (it's an executable without .py extension)
import importlib.util
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

module_path = Path(__file__).parent / "ai-maintainer"
loader = importlib.machinery.SourceFileLoader("ai_maintainer", str(module_path))
spec = importlib.util.spec_from_loader("ai_maintainer", loader)
gm = importlib.util.module_from_spec(spec)
sys.modules["ai_maintainer"] = gm
spec.loader.exec_module(gm)


@pytest.fixture
def default_config():
    """Create a default Config for testing."""
    return gm.Config(
        agent_command="echo",
        agent_flags="",
        agent_timeout_seconds=300,
        auto_merge_dependabot=True,
        auto_update_dependencies=True,
        ci_timeout_minutes=10,
        command_timeout_seconds=120,
        dependency_min_age_days=30,
        dry_run=True,
        exclude=set(),
        max_fix_attempts=4,
        push_changes=False,
        rollback_on_ci_failure=False,
        run_tests=True,
        test_timeout_seconds=600,
    )


@pytest.fixture
def repo_path(tmp_path):
    """A directory that looks like a git repository."""
    (tmp_path / ".git").mkdir()
    return tmp_path


@pytest.fixture
def maintainer(repo_path, default_config):
    """A Maintainer on an empty git repository with the default (dry-run) config."""
    return gm.Maintainer(repo_path, default_config)


@pytest.fixture
def agent_client(default_config):
    return gm.AgentClient(Path("/tmp"), "test-repo", default_config, MagicMock())


def make_maintainer(repo_path, config, **overrides):
    """Build a Maintainer with Config field overrides (e.g. dry_run=False)."""
    if overrides:
        config = gm.Config(**{**config.__dict__, **overrides})
    return gm.Maintainer(repo_path, config)


class TestSafeJsonParse:
    """Tests for safe_json_parse function."""

    def test_valid_json(self):
        result = gm.safe_json_parse('{"key": "value"}')
        assert result == {"key": "value"}

    def test_valid_json_array(self):
        result = gm.safe_json_parse("[1, 2, 3]")
        assert result == [1, 2, 3]

    def test_invalid_json_returns_default(self):
        result = gm.safe_json_parse("not json")
        assert result is None

    def test_invalid_json_returns_custom_default(self):
        result = gm.safe_json_parse("not json", default=[])
        assert result == []

    def test_empty_string_returns_default(self):
        result = gm.safe_json_parse("", default={})
        assert result == {}


class TestProjectEnvironment:
    """Tests for ProjectEnvironment class."""

    def test_no_version_files(self, tmp_path):
        assert gm.ProjectEnvironment(tmp_path).env_runner is None

    def test_nvmrc_detection(self, tmp_path):
        (tmp_path / ".nvmrc").write_text("18.0.0")
        env = gm.ProjectEnvironment(tmp_path)
        # None if fnm/nvm are not installed; otherwise a shell prefix
        assert env.env_runner is None or (
            "fnm" in env.env_runner or "nvm" in env.env_runner
        )

    def test_pipfile_detection(self, tmp_path):
        (tmp_path / "Pipfile").write_text("[packages]")
        assert gm.ProjectEnvironment(tmp_path).env_runner == "pipenv run"

    def test_poetry_detection(self, tmp_path):
        (tmp_path / "poetry.lock").write_text("")
        assert gm.ProjectEnvironment(tmp_path).env_runner == "poetry run"

    def test_go_detection_no_crash(self, tmp_path):
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        env = gm.ProjectEnvironment(tmp_path)
        # None if go is already on PATH, else a PATH-export prefix
        assert env.env_runner is None or env.env_runner.startswith('export PATH="')

    def test_go_runner_when_not_on_path(self, tmp_path, monkeypatch):
        (tmp_path / "go.mod").write_text("module example.com/foo\n")
        # Pretend go is not on PATH so it falls back to known install dirs
        monkeypatch.setattr(gm.shutil, "which", lambda name: None)
        # True for the real go.mod and the simulated go binary location
        real_exists = Path.exists
        monkeypatch.setattr(
            Path,
            "exists",
            lambda self: str(self).endswith("/usr/local/go/bin/go")
            or real_exists(self),
        )
        env = gm.ProjectEnvironment(tmp_path)
        assert (
            env.env_runner
            == 'export PATH="/usr/local/go/bin:$HOME/go/bin:$PATH" &&'
        )


class TestAgentClientParseJson:
    """Tests for AgentClient.parse_json."""

    def test_plain_json(self, agent_client):
        assert agent_client.parse_json('{"key": "value"}') == {"key": "value"}

    def test_json_from_markdown_block(self, agent_client):
        response = """Here's the result:
```json
{"should_update": true, "commands": ["npm update"]}
```
"""
        assert agent_client.parse_json(response) == {
            "should_update": True,
            "commands": ["npm update"],
        }

    def test_json_from_code_block_without_lang(self, agent_client):
        response = """```
{"fixed": false}
```"""
        assert agent_client.parse_json(response) == {"fixed": False}

    def test_json_with_text_before(self, agent_client):
        response = """Some explanation text here.

{"updated": false, "changes_made": "", "reasoning": "All up to date"}"""
        assert agent_client.parse_json(response) == {
            "updated": False,
            "changes_made": "",
            "reasoning": "All up to date",
        }

    def test_json_with_text_after(self, agent_client):
        response = '{"fixed": true, "changes_made": "bumped"}\n\nDone, hope that helps!'
        assert agent_client.parse_json(response) == {
            "fixed": True,
            "changes_made": "bumped",
        }

    def test_non_json_code_block_falls_through_to_json(self, agent_client):
        response = """I ran:
```bash
npm update lodash
```
{"updated": true, "changes_made": "bumped lodash"}"""
        assert agent_client.parse_json(response) == {
            "updated": True,
            "changes_made": "bumped lodash",
        }

    def test_empty_response(self, agent_client):
        assert agent_client.parse_json("") is None

    def test_no_json_found(self, agent_client):
        assert agent_client.parse_json("Just plain text with no JSON") is None


class TestRunCommand:
    """Tests for run_command function."""

    def test_successful_command(self):
        success, stdout, stderr = gm.run_command(["echo", "hello"], Path("/tmp"))
        assert success is True
        assert stdout.strip() == "hello"
        assert stderr == ""

    def test_failed_command(self):
        success, stdout, stderr = gm.run_command(["false"], Path("/tmp"))
        assert success is False

    def test_nonexistent_command(self):
        success, stdout, stderr = gm.run_command(
            ["nonexistent_command_12345"], Path("/tmp")
        )
        assert success is False

    def test_shell_command(self):
        success, stdout, stderr = gm.run_shell_command(
            "echo hello && echo world", Path("/tmp")
        )
        assert success is True
        assert "hello" in stdout
        assert "world" in stdout


class TestConfig:
    """Tests for Config dataclass."""

    def test_config_creation(self):
        config = gm.Config(
            agent_command="claude",
            agent_flags="--dangerously-skip-permissions",
            agent_timeout_seconds=300,
            auto_merge_dependabot=True,
            auto_update_dependencies=True,
            ci_timeout_minutes=10,
            command_timeout_seconds=120,
            dependency_min_age_days=30,
            dry_run=False,
            exclude={"excluded-repo"},
            max_fix_attempts=4,
            push_changes=True,
            rollback_on_ci_failure=False,
            run_tests=True,
            test_timeout_seconds=600,
        )
        assert config.agent_command == "claude"
        assert config.dependency_min_age_days == 30
        assert "excluded-repo" in config.exclude


class TestGitClient:
    """Tests for GitClient class."""

    def test_is_git_repo_true(self, repo_path):
        assert gm.GitClient(repo_path, MagicMock()).is_git_repo() is True

    def test_is_git_repo_false(self, tmp_path):
        assert gm.GitClient(tmp_path, MagicMock()).is_git_repo() is False

    def test_repo_name(self):
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        assert client.repo_name == "my-repo"

    def test_has_unpushed_commits_false_when_no_upstream(self):
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        client._run = MagicMock(
            return_value=(False, "", "fatal: no upstream configured")
        )
        assert client.has_unpushed_commits() is False

    def test_latest_commit_from_maintainer_squash_merge_attribution(self):
        # A squash merge performed by this tool carries the attribution body
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        message = f"chore(deps): bump lodash (#12)\n\n{gm.COMMIT_ATTRIBUTION}\n"
        client._run = MagicMock(return_value=(True, message, ""))
        assert client.is_latest_commit_from_maintainer() is True

    def test_latest_commit_not_from_maintainer_without_attribution(self):
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        for message in (
            # A human squash merge of a dependabot PR has no attribution body
            "chore(deps): bump lodash from 1.0.0 to 1.0.1 (#12)\n",
            # Mentioning the tool's name is not attribution
            "ci: stop running ai-maintainer nightly\n",
            "chore(deps): bump ai-maintainer from 0.1.0 to 0.2.0 (#12)\n",
        ):
            client._run = MagicMock(return_value=(True, message, ""))
            assert client.is_latest_commit_from_maintainer() is False, message

    def test_latest_commit_ci_fix(self):
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        message = (
            f"{gm.CI_FIX_COMMIT_TITLE}\n\nFixed CI build failure with AI "
            f"assistance\n\n{gm.COMMIT_ATTRIBUTION}\n"
        )
        client._run = MagicMock(return_value=(True, message, ""))
        assert client.is_latest_commit_ci_fix() is True

    def test_latest_commit_ci_fix_false_for_other_maintainer_commits(self):
        # Attributed, but a dependency update rather than a CI fix
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        message = (
            "chore(deps): update direct dependencies\n\nUpdated direct "
            f"dependencies\n\n{gm.COMMIT_ATTRIBUTION}\n"
        )
        client._run = MagicMock(return_value=(True, message, ""))
        assert client.is_latest_commit_ci_fix() is False


class TestFindRepos:
    """Tests for find_repos function."""

    def test_base_dir_is_git_repo(self, repo_path):
        repos = gm.find_repos(repo_path, MagicMock())
        assert repos == [repo_path]

    def test_finds_subdirectory_repos(self, tmp_path):
        # Create two git repos as subdirectories
        (tmp_path / "repo-a" / ".git").mkdir(parents=True)
        (tmp_path / "repo-b" / ".git").mkdir(parents=True)
        # Create a non-repo directory
        (tmp_path / "not-a-repo").mkdir()
        repos = gm.find_repos(tmp_path, MagicMock())
        assert len(repos) == 2
        assert (tmp_path / "repo-a") in repos
        assert (tmp_path / "repo-b") in repos

    def test_empty_directory(self, tmp_path):
        repos = gm.find_repos(tmp_path, MagicMock())
        assert repos == []


class TestMaintainerValidation:
    """Tests for Maintainer validation methods."""

    def test_is_valid_dependabot_pr_valid_branch(self, maintainer):
        maintainer.github.is_commit_verified = MagicMock(return_value=True)
        pr = {
            "number": 123,
            "headRefName": "dependabot/npm_and_yarn/lodash-4.17.21",
            "headRefOid": "abc123",
        }
        assert maintainer._is_valid_dependabot_pr(pr) is True

    def test_is_valid_dependabot_pr_invalid_branch(self, maintainer):
        pr = {
            "number": 123,
            "headRefName": "feature/some-feature",
            "headRefOid": "abc123",
        }
        assert maintainer._is_valid_dependabot_pr(pr) is False

    def test_is_valid_dependabot_pr_unverified_commit(self, maintainer):
        maintainer.github.is_commit_verified = MagicMock(return_value=False)
        pr = {
            "number": 123,
            "headRefName": "dependabot/npm_and_yarn/lodash-4.17.21",
            "headRefOid": "abc123",
        }
        assert maintainer._is_valid_dependabot_pr(pr) is False


class TestDetectTestCommand:
    """Tests for test command detection."""

    def test_detect_npm_test(self, repo_path, default_config):
        (repo_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
        maintainer = gm.Maintainer(repo_path, default_config)
        assert maintainer.detect_test_command() == "npm test"

    def test_detect_pytest(self, repo_path, default_config):
        (repo_path / "pyproject.toml").write_text("[tool.pytest]")
        (repo_path / "tests").mkdir()
        maintainer = gm.Maintainer(repo_path, default_config)
        assert maintainer.detect_test_command() == "pytest"

    def test_detect_cargo_test(self, repo_path, default_config):
        (repo_path / "Cargo.toml").write_text("[package]")
        maintainer = gm.Maintainer(repo_path, default_config)
        assert maintainer.detect_test_command().endswith("cargo test")

    def test_detect_go_test(self, repo_path, default_config):
        (repo_path / "go.mod").write_text("module example.com/foo\n")
        maintainer = gm.Maintainer(repo_path, default_config)
        assert maintainer.detect_test_command().endswith("go test ./...")

    def test_detect_no_test(self, maintainer):
        assert maintainer.detect_test_command() is None


class TestCommitAndPushEnvRunner:
    """commit_and_push runs git through the project env runner so hooks
    (pre-commit/pre-push) resolve the same toolchain used for testing."""

    def _maintainer(self, repo_path, default_config, env_runner):
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=True
        )
        # Force the env runner as if .nvmrc + nvm were present
        maintainer.git.env_runner = env_runner
        maintainer.git.get_head_sha = MagicMock(return_value="abc1234")
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        maintainer.git.has_unpushed_commits = MagicMock(return_value=True)
        maintainer.github.repo_url = None
        return maintainer

    def test_commit_and_push_use_env_runner_prefix(
        self, repo_path, default_config, monkeypatch
    ):
        prefix = "source ~/.nvm/nvm.sh && nvm use &&"
        maintainer = self._maintainer(repo_path, default_config, prefix)

        shell_calls = []

        def fake_run_command(cmd, cwd, *a, **k):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return True, "package.json\n", ""
            # git commit / git push must NOT go through here when prefixed
            assert cmd[:2] not in (["git", "commit"], ["git", "push"])
            return True, "", ""

        def fake_run_shell_command(cmd, cwd, *a, **k):
            shell_calls.append(cmd)
            return True, "", ""

        monkeypatch.setattr(gm, "run_command", fake_run_command)
        monkeypatch.setattr(gm, "run_shell_command", fake_run_shell_command)

        success, had_changes = maintainer.commit_and_push("fix: bump deps")

        assert success is True
        assert had_changes is True
        assert any(
            c.startswith(f"{prefix} git commit -m ") and "fix: bump deps" in c
            for c in shell_calls
        )
        assert f"{prefix} git push" in shell_calls

    def test_commit_and_push_without_env_runner(
        self, repo_path, default_config, monkeypatch
    ):
        maintainer = self._maintainer(repo_path, default_config, None)

        commit_calls = []

        def fake_run_command(cmd, cwd, *a, **k):
            if cmd[:3] == ["git", "diff", "--cached"]:
                return True, "package.json\n", ""
            if cmd[:2] == ["git", "commit"]:
                commit_calls.append(cmd)
            return True, "", ""

        def fake_run_shell_command(cmd, cwd, *a, **k):
            raise AssertionError("env_runner is None; should not use a shell")

        monkeypatch.setattr(gm, "run_command", fake_run_command)
        monkeypatch.setattr(gm, "run_shell_command", fake_run_shell_command)

        success, had_changes = maintainer.commit_and_push("fix: bump deps")

        assert success is True
        assert commit_calls == [["git", "commit", "-m", "fix: bump deps"]]


class TestRunGit:
    """Tests for the run_git helper."""

    def test_no_env_runner_uses_run_command(self, monkeypatch):
        calls = []

        def fake_run_command(cmd, cwd, **k):
            calls.append(cmd)
            return True, "", ""

        def fail_shell(*a, **k):
            raise AssertionError("env_runner is None; should not use a shell")

        monkeypatch.setattr(gm, "run_command", fake_run_command)
        monkeypatch.setattr(gm, "run_shell_command", fail_shell)

        ok, _, _ = gm.run_git(["push"], Path("/tmp"))
        assert ok is True
        assert calls == [["git", "push"]]

    def test_env_runner_uses_shell_and_quotes(self, monkeypatch):
        shell = []

        def fake_run_shell_command(cmd, cwd, **k):
            shell.append(cmd)
            return True, "", ""

        monkeypatch.setattr(gm, "run_shell_command", fake_run_shell_command)

        ok, _, _ = gm.run_git(["commit", "-m", "a b"], Path("/tmp"), "nvm use &&")
        assert ok is True
        assert shell == ["nvm use && git commit -m 'a b'"]

    def test_gitclient_is_writable_uses_env_runner(self, tmp_path, monkeypatch):
        client = gm.GitClient(tmp_path, MagicMock(), "nvm use &&")
        shell = []

        def fake_run_shell_command(cmd, cwd, **k):
            shell.append(cmd)
            return True, "", ""

        monkeypatch.setattr(gm, "run_shell_command", fake_run_shell_command)
        assert client.is_writable() is True
        assert shell == ["nvm use && git push --dry-run"]

    def test_gitclient_pull_uses_env_runner(self, tmp_path, monkeypatch):
        client = gm.GitClient(tmp_path, MagicMock(), "nvm use &&")
        shell = []

        def fake_run_shell_command(cmd, cwd, **k):
            shell.append(cmd)
            return True, "", ""

        monkeypatch.setattr(gm, "run_shell_command", fake_run_shell_command)
        assert client.pull_changes() is True
        assert shell == ["nvm use && git pull"]


class TestGitHubClientMergePr:
    """merge_pr stamps the squash commit so later runs can recognize it."""

    def _client_with_pr(self, tmp_path, payload, calls):
        client = gm.GitHubClient(tmp_path, MagicMock(), MagicMock())

        def fake_run(args):
            calls.append(args)
            if args[:3] == ["gh", "pr", "view"]:
                return True, json.dumps(payload), ""
            return True, "", ""

        client._run = fake_run
        return client

    def _client_with_pr_body(self, tmp_path, pr_body, calls):
        return self._client_with_pr(tmp_path, {"body": pr_body}, calls)

    def test_merge_pr_appends_attribution_to_pr_body(self, tmp_path):
        calls = []
        client = self._client_with_pr_body(
            tmp_path, "Bumps lodash from 1 to 2.", calls
        )
        success, error = client.merge_pr(42)
        assert success is True
        assert error == ""
        merge_args = calls[-1]
        assert merge_args[:3] == ["gh", "pr", "merge"]
        assert "--squash" in merge_args
        body = merge_args[merge_args.index("--body") + 1]
        assert body == f"Bumps lodash from 1 to 2.\n\n{gm.COMMIT_ATTRIBUTION}"

    def test_merge_pr_attribution_only_when_pr_body_empty(self, tmp_path):
        calls = []
        client = self._client_with_pr_body(tmp_path, "", calls)
        success, _ = client.merge_pr(42)
        assert success is True
        merge_args = calls[-1]
        assert merge_args[merge_args.index("--body") + 1] == gm.COMMIT_ATTRIBUTION

    def test_merge_pr_preserves_coauthors_excluding_pr_author(self, tmp_path):
        calls = []
        alice = {"login": "alice", "name": "Alice", "email": "alice@example.com"}
        payload = {
            "body": "Bumps lodash.",
            "author": {"login": "dependabot"},
            "commits": [
                {
                    "authors": [
                        {
                            "login": "dependabot",
                            "name": "dependabot[bot]",
                            "email": "support@github.com",
                        }
                    ]
                },
                # A human follow-up commit, listed twice to verify dedup
                {"authors": [alice]},
                {"authors": [alice]},
            ],
        }
        client = self._client_with_pr(tmp_path, payload, calls)
        success, _ = client.merge_pr(42)
        assert success is True
        merge_args = calls[-1]
        body = merge_args[merge_args.index("--body") + 1]
        assert body == (
            "Bumps lodash.\n\n"
            f"{gm.COMMIT_ATTRIBUTION}\n\n"
            "Co-authored-by: Alice <alice@example.com>"
        )

    def test_merge_pr_strips_newlines_from_coauthor_fields(self, tmp_path):
        calls = []
        payload = {
            "body": "Bumps lodash.",
            "author": {"login": "dependabot"},
            "commits": [
                {
                    "authors": [
                        {
                            "login": "mallory",
                            "name": "Mal\nCo-authored-by: Fake <fake@example.com>",
                            "email": "mal@example.com",
                        }
                    ]
                }
            ],
        }
        client = self._client_with_pr(tmp_path, payload, calls)
        client.merge_pr(42)
        merge_args = calls[-1]
        body = merge_args[merge_args.index("--body") + 1]
        # The forged trailer must be folded into the real one's line so git
        # cannot parse it as a second trailer
        trailer_lines = [
            line for line in body.splitlines() if line.startswith("Co-authored-by:")
        ]
        assert len(trailer_lines) == 1
        assert trailer_lines[0].endswith("<mal@example.com>")

    def test_merge_pr_strips_angle_brackets_from_coauthor_name(self, tmp_path):
        # An embedded "<addr>" would otherwise be read as the trailer identity
        calls = []
        payload = {
            "body": "Bumps lodash.",
            "author": {"login": "dependabot"},
            "commits": [
                {
                    "authors": [
                        {
                            "login": "mallory",
                            "name": "Bob <evil@example.com>",
                            "email": "real@example.com",
                        }
                    ]
                }
            ],
        }
        client = self._client_with_pr(tmp_path, payload, calls)
        client.merge_pr(42)
        merge_args = calls[-1]
        body = merge_args[merge_args.index("--body") + 1]
        assert "evil@example.com" in body  # kept as inert text
        assert body.endswith("Co-authored-by: Bob evil@example.com <real@example.com>")


class TestMergePrsOnGithub:
    """Tests for _merge_prs_on_github method."""

    def test_dry_run_skips_merge(self, maintainer):
        assert maintainer._merge_prs_on_github([1, 2]) == [1, 2]

    def test_merge_success(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(return_value=(True, ""))
        assert maintainer._merge_prs_on_github([1, 2]) == [1, 2]
        assert maintainer.github.merge_pr.call_count == 2

    def test_merge_partial_failure(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            side_effect=[(True, ""), (False, "merge blocked"), (True, "")]
        )
        assert maintainer._merge_prs_on_github([1, 2, 3]) == [1, 3]

    def test_empty_list(self, maintainer):
        assert maintainer._merge_prs_on_github([]) == []

    def test_merge_conflict_triggers_rebase(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            return_value=(
                False,
                "GraphQL: Pull Request has merge conflicts (mergePullRequest)",
            )
        )
        maintainer.github.get_recent_pr_comment_bodies = MagicMock(return_value=[])
        maintainer.github.comment_pr = MagicMock(return_value=(True, ""))
        assert maintainer._merge_prs_on_github([5]) == []
        maintainer.github.comment_pr.assert_called_once_with(
            5, gm.DEPENDABOT_REBASE_COMMAND
        )

    def test_merge_conflict_skips_rebase_when_already_requested(
        self, repo_path, default_config
    ):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            return_value=(
                False,
                "GraphQL: Pull Request has merge conflicts (mergePullRequest)",
            )
        )
        maintainer.github.get_recent_pr_comment_bodies = MagicMock(
            return_value=["please review", gm.DEPENDABOT_REBASE_COMMAND]
        )
        maintainer.github.comment_pr = MagicMock(return_value=(True, ""))
        assert maintainer._merge_prs_on_github([5]) == []
        maintainer.github.comment_pr.assert_not_called()

    def test_already_merged_counts_as_merged(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            return_value=(True, "! Pull request #9 was already merged")
        )
        maintainer.github.comment_pr = MagicMock(return_value=True)
        assert maintainer._merge_prs_on_github([9]) == [9]
        maintainer.github.comment_pr.assert_not_called()

    def test_non_conflict_failure_skips_without_rebase(
        self, repo_path, default_config
    ):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            return_value=(False, "merge blocked by branch protection")
        )
        maintainer.github.comment_pr = MagicMock(return_value=True)
        assert maintainer._merge_prs_on_github([7]) == []
        maintainer.github.comment_pr.assert_not_called()


class TestGitHubClientCi:
    """CI status handling must consider every workflow run for a commit."""

    def _client(self, tmp_path):
        client = gm.GitHubClient(tmp_path, MagicMock(), MagicMock())
        client.is_authenticated = True
        return client

    def test_combine_all_success(self):
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "success"},
        ]
        assert gm.GitHubClient._combine_run_conclusions(runs) == "success"

    def test_combine_any_failure_wins(self):
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "failure"},
        ]
        assert gm.GitHubClient._combine_run_conclusions(runs) == "failure"

    def test_combine_failure_beats_in_progress(self):
        runs = [
            {"status": "in_progress", "conclusion": None},
            {"status": "completed", "conclusion": "failure"},
        ]
        assert gm.GitHubClient._combine_run_conclusions(runs) == "failure"

    def test_combine_pending_run_is_in_progress(self):
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "in_progress", "conclusion": None},
        ]
        assert gm.GitHubClient._combine_run_conclusions(runs) == "in_progress"

    def test_conclusion_scoped_to_head_sha(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "success", "headSha": "new"},
                {"status": "completed", "conclusion": "failure", "headSha": "old"},
            ]
        )
        assert client.get_ci_conclusion("old") == "failure"

    def test_conclusion_falls_back_when_head_sha_has_no_runs(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "success", "headSha": "other"},
            ]
        )
        # Still non-None, so callers keep monitoring after their own push
        assert client.get_ci_conclusion("mine") == "success"

    def test_fallback_surfaces_a_pre_existing_failure(self, tmp_path):
        # paths-filtered workflows leave most commits with no runs, so a real
        # failure on an earlier commit must not read as an unknown-healthy
        # baseline the tool would then answer for
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "failure", "headSha": "older"},
            ]
        )
        assert client.get_ci_conclusion("mine") == "failure"

    def test_fallback_uses_only_the_newest_commits_runs(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "success", "headSha": "newer"},
                {"status": "completed", "conclusion": "failure", "headSha": "older"},
            ]
        )
        # An older commit's failure must not be attributed to the newest state
        assert client.get_ci_conclusion("mine") == "success"

    def test_conclusion_none_when_branch_has_no_runs(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(return_value=[])
        assert client.get_ci_conclusion("mine") is None

    def test_wait_for_ci_does_not_sleep_after_the_final_poll(
        self, tmp_path, monkeypatch
    ):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(return_value=[])
        sleeps = []
        monkeypatch.setattr(gm.time, "sleep", lambda s: sleeps.append(s))
        # 1 minute at a 30s interval is 2 polls, so only 1 sleep between them
        assert client.wait_for_ci("abc", 1) is None
        assert len(sleeps) == 1

    def test_combine_no_runs_is_not_success(self):
        # This verdict gates a force push, so "no evidence" must not read as a pass
        assert gm.GitHubClient._combine_run_conclusions([]) is None

    def test_failed_run_id_skips_a_newer_successful_run(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"databaseId": 3, "status": "completed",
                 "conclusion": "success", "headSha": "abc"},
                {"databaseId": 2, "status": "completed",
                 "conclusion": "failure", "headSha": "abc"},
            ]
        )
        assert client.get_latest_failed_run_id("abc") == 2

    def test_failed_run_id_prefers_the_run_for_head_sha(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"databaseId": 5, "status": "completed",
                 "conclusion": "failure", "headSha": "old"},
                {"databaseId": 4, "status": "completed",
                 "conclusion": "failure", "headSha": "abc"},
            ]
        )
        assert client.get_latest_failed_run_id("abc") == 4

    def test_failed_run_id_none_when_nothing_failed(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"databaseId": 3, "status": "completed",
                 "conclusion": "success", "headSha": "abc"},
            ]
        )
        assert client.get_latest_failed_run_id("abc") is None

    def test_failure_logs_come_from_the_failed_run(self, tmp_path):
        client = self._client(tmp_path)
        client.get_latest_failed_run_id = MagicMock(return_value=77)
        calls = []

        def fake_run(args):
            calls.append(args)
            return True, "boom", ""

        client._run = fake_run
        assert client.get_ci_failure_logs("abc") == "boom"
        assert "77" in calls[0]

    def test_combine_non_success_conclusion_surfaces(self):
        runs = [
            {"status": "completed", "conclusion": "success"},
            {"status": "completed", "conclusion": "cancelled"},
        ]
        assert gm.GitHubClient._combine_run_conclusions(runs) == "cancelled"

    def test_latest_conclusion_ignores_older_commits(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "success", "headSha": "new"},
                {"status": "completed", "conclusion": "failure", "headSha": "new"},
                {"status": "completed", "conclusion": "failure", "headSha": "old"},
            ]
        )
        assert client.get_ci_conclusion() == "failure"

    def test_latest_conclusion_all_workflows_must_pass(self, tmp_path):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "success", "headSha": "new"},
                {"status": "completed", "conclusion": "success", "headSha": "new"},
            ]
        )
        assert client.get_ci_conclusion() == "success"

    def test_wait_for_ci_fails_when_any_workflow_fails(self, tmp_path, monkeypatch):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            return_value=[
                {"status": "completed", "conclusion": "success", "headSha": "abc"},
                {"status": "completed", "conclusion": "failure", "headSha": "abc"},
            ]
        )
        monkeypatch.setattr(gm.time, "sleep", lambda s: None)
        assert client.wait_for_ci("abc", 1) == "failure"

    def test_wait_for_ci_keeps_polling_while_a_workflow_runs(
        self, tmp_path, monkeypatch
    ):
        client = self._client(tmp_path)
        client._get_runs = MagicMock(
            side_effect=[
                [
                    {"status": "completed", "conclusion": "success", "headSha": "abc"},
                    {"status": "in_progress", "conclusion": None, "headSha": "abc"},
                ],
                [
                    {"status": "completed", "conclusion": "success", "headSha": "abc"},
                    {"status": "completed", "conclusion": "success", "headSha": "abc"},
                ],
            ]
        )
        monkeypatch.setattr(gm.time, "sleep", lambda s: None)
        assert client.wait_for_ci("abc", 1) == "success"

    def test_get_runs_over_fetches_before_filtering(self, tmp_path):
        client = self._client(tmp_path)
        client._git.current_branch = "main"
        captured = {}

        def fake_run(args):
            captured["args"] = args
            runs = [
                {
                    "workflowName": "Dependabot Updates",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "workflowName": "CI",
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
            return True, json.dumps(runs), ""

        client._run = fake_run
        runs = client._get_runs(1)
        # The ignored workflow must not consume the single result slot
        assert len(runs) == 1
        assert runs[0]["workflowName"] == "CI"
        limit = captured["args"][captured["args"].index("--limit") + 1]
        assert int(limit) > 1

    @pytest.mark.parametrize(
        "remote",
        [
            "git@github.com:owner/repo.git",
            "https://github.com/owner/repo",
            "https://token@github.com/owner/repo.git",
            "ssh://git@github.com/owner/repo.git",
            "ssh://git@github.com:22/owner/repo",
            "git://github.com/owner/repo",
            "https://github.com/owner/repo/",
        ],
    )
    def test_github_remote_forms_resolve(self, tmp_path, remote):
        git = MagicMock()
        git.get_remote_url.return_value = remote
        client = gm.GitHubClient(tmp_path, git, MagicMock())
        assert client.owner_repo == "owner/repo"
        assert client.repo_url == "https://github.com/owner/repo"

    @pytest.mark.parametrize(
        "remote",
        [
            "https://gitlab.com/owner/repo.git",
            "git@bitbucket.org:owner/repo.git",
            "https://github.example.com/owner/repo",
            "",
        ],
    )
    def test_non_github_remotes_do_not_resolve(self, tmp_path, remote):
        git = MagicMock()
        git.get_remote_url.return_value = remote
        client = gm.GitHubClient(tmp_path, git, MagicMock())
        assert client.owner_repo is None
        assert client.repo_url is None


class TestUpdateDependencies:
    """The workdir state is verified against the agent's reported outcome."""

    def _maintainer(self, repo_path, default_config, decision):
        (repo_path / "package.json").write_text("{}")
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.agent.ask_json = MagicMock(return_value=decision)
        maintainer.git.reset_changes = MagicMock(return_value=True)
        return maintainer

    def test_unreported_changes_are_discarded(self, repo_path, default_config):
        maintainer = self._maintainer(
            repo_path, default_config, {"updated": False, "reasoning": "none needed"}
        )
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        success, had_updates = maintainer.update_dependencies()
        assert success is True
        assert had_updates is False
        maintainer.git.reset_changes.assert_called_once()

    def test_no_updates_clean_workdir_no_reset(self, repo_path, default_config):
        maintainer = self._maintainer(
            repo_path, default_config, {"updated": False, "reasoning": "none needed"}
        )
        maintainer.git.is_workdir_clean = MagicMock(return_value=True)
        success, had_updates = maintainer.update_dependencies()
        assert success is True
        assert had_updates is False
        maintainer.git.reset_changes.assert_not_called()

    def test_reported_updates_with_changes(self, repo_path, default_config):
        maintainer = self._maintainer(
            repo_path,
            default_config,
            {"updated": True, "changes_made": "bumped", "reasoning": "outdated"},
        )
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        success, had_updates = maintainer.update_dependencies()
        assert success is True
        assert had_updates is True
        maintainer.git.reset_changes.assert_not_called()


class TestMaintainMergedPrCiMonitoring:
    """Merged dependabot PRs must be CI-monitored even when the run aborts."""

    def _maintainer(self, repo_path, default_config):
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=True
        )
        maintainer._validate_repo = MagicMock(return_value=True)
        maintainer._check_and_fix_pre_existing_ci = MagicMock(
            return_value=(True, True)
        )
        maintainer.git.get_head_sha = MagicMock(return_value="base123")
        maintainer.git.get_upstream_sha = MagicMock(return_value="base123")
        maintainer.merge_dependabot_prs = MagicMock(return_value=[42])
        maintainer.git.pull_changes = MagicMock(return_value=True)
        maintainer.git.reset_changes = MagicMock(return_value=True)
        maintainer._handle_post_push_ci = MagicMock(return_value=False)
        return maintainer

    def test_unfixable_test_failure_still_monitors_ci(
        self, repo_path, default_config
    ):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(return_value=(True, False))
        maintainer.run_tests = MagicMock(return_value=(False, "boom"))
        maintainer.fix_test_with_retries = MagicMock(return_value=False)
        status, had_changes = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        assert had_changes is False
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )

    def test_failed_dependency_update_still_monitors_ci(
        self, repo_path, default_config
    ):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(return_value=(False, False))
        status, had_changes = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        assert had_changes is False
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )


    def test_ci_is_monitored_even_when_the_reset_fails(
        self, repo_path, default_config
    ):
        # A tree that cannot be cleaned still leaves merged PRs on the remote
        # with CI running, so observation and rollback must survive
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(return_value=(False, False))
        maintainer.git.reset_changes = MagicMock(return_value=False)
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )

    def test_ci_is_monitored_when_update_dependencies_itself_raises(
        self, repo_path, default_config
    ):
        # update_dependencies resets internally, so its WorkingTreeError is
        # raised before _maintain_after_merge's own reset is reached
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(
            side_effect=gm.WorkingTreeError("boom")
        )
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )

    def test_ci_is_monitored_when_the_test_fix_raises(
        self, repo_path, default_config
    ):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(return_value=(True, True))
        maintainer.run_tests = MagicMock(return_value=(False, "boom"))
        maintainer.fix_test_with_retries = MagicMock(
            side_effect=gm.WorkingTreeError("boom")
        )
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )

    def test_failed_pull_after_merge_still_monitors_ci(
        self, repo_path, default_config
    ):
        # The PRs are squash-merged on GitHub with CI running even though the
        # pull that would bring them local failed
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.git.pull_changes = MagicMock(return_value=False)
        maintainer.git.fetch = MagicMock(return_value=True)
        maintainer.git.get_upstream_sha = MagicMock(return_value="merge456")
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        maintainer.git.fetch.assert_called_once()
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "merge456"
        )

    def test_failed_pull_does_not_monitor_the_unchanged_baseline(
        self, repo_path, default_config
    ):
        # The pull failed before fetching, so the tracking ref still points at
        # the pre-merge commit and there is nothing new to monitor
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.git.pull_changes = MagicMock(return_value=False)
        maintainer.git.fetch = MagicMock(return_value=False)
        maintainer.git.get_upstream_sha = MagicMock(return_value="base123")
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        maintainer._handle_post_push_ci.assert_not_called()

    def test_ci_is_not_monitored_twice_when_the_fix_path_raises(
        self, repo_path, default_config
    ):
        # _handle_post_push_ci can raise via its own AI fix; re-entering it
        # would spend a second full CI timeout and force push a second rollback
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(return_value=(True, True))
        maintainer.run_tests = MagicMock(return_value=(True, ""))
        maintainer.commit_and_push = MagicMock(return_value=(True, True))

        def monitor(*args):
            maintainer._ci_monitored = True
            raise gm.WorkingTreeError("boom")

        maintainer._handle_post_push_ci = MagicMock(side_effect=monitor)
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        assert maintainer._handle_post_push_ci.call_count == 1

    def test_push_failure_still_monitors_merged_pr_ci(
        self, repo_path, default_config
    ):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.update_dependencies = MagicMock(return_value=(True, True))
        maintainer.run_tests = MagicMock(return_value=(True, ""))
        maintainer.commit_and_push = MagicMock(return_value=(False, False))
        status, _ = maintainer.maintain()
        assert status == gm.STATUS_FAILED
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )

    def test_unresolvable_upstream_falls_back_to_head(
        self, repo_path, default_config
    ):
        # Merged PRs are on the remote; losing monitoring entirely is worse
        # than monitoring HEAD, which is the merge commit at this point
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.git.get_upstream_sha = MagicMock(return_value=None)
        maintainer.update_dependencies = MagicMock(return_value=(False, False))
        maintainer.maintain()
        maintainer._handle_post_push_ci.assert_called_once_with(
            "base123", True, "base123"
        )

    def test_merge_commit_is_monitored_when_push_is_disabled(
        self, repo_path, default_config
    ):
        # --no-push commits locally, moving HEAD off the merge commit. CI runs
        # on the merge commit, so monitoring HEAD would poll a SHA that has no
        # runs until the CI timeout expires.
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=False
        )
        maintainer._validate_repo = MagicMock(return_value=True)
        maintainer._check_and_fix_pre_existing_ci = MagicMock(
            return_value=(True, True)
        )
        maintainer.merge_dependabot_prs = MagicMock(return_value=[42])
        maintainer.git.pull_changes = MagicMock(return_value=True)
        maintainer.git.get_head_sha = MagicMock(return_value="local789")
        maintainer.git.get_upstream_sha = MagicMock(return_value="merge456")
        maintainer.update_dependencies = MagicMock(return_value=(True, True))
        maintainer.run_tests = MagicMock(return_value=(True, ""))
        maintainer.commit_and_push = MagicMock(return_value=(True, True))
        maintainer._handle_post_push_ci = MagicMock(return_value=True)
        maintainer.maintain()
        # The upstream SHA, not local HEAD ("local789"), is what CI runs on
        maintainer._handle_post_push_ci.assert_called_once_with(
            "local789", True, "merge456"
        )


class TestCheckAndFixPreExistingCi:
    """A failed CI fix from a previous run must not be retried every run."""

    def test_failed_ci_fix_not_retried(self, repo_path, default_config):
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=True
        )
        maintainer.github.get_ci_conclusion = MagicMock(
            return_value="failure"
        )
        maintainer.git.is_latest_commit_from_maintainer = MagicMock(
            return_value=True
        )
        maintainer.git.is_latest_commit_ci_fix = MagicMock(return_value=True)
        maintainer.fix_ci_with_retries = MagicMock()
        should_continue, ci_was_passing = maintainer._check_and_fix_pre_existing_ci()
        assert should_continue is True
        assert ci_was_passing is False
        maintainer.fix_ci_with_retries.assert_not_called()

    def test_non_fix_maintainer_commit_still_fixed(self, repo_path, default_config):
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=True
        )
        maintainer.github.get_ci_conclusion = MagicMock(
            return_value="failure"
        )
        maintainer.git.is_latest_commit_from_maintainer = MagicMock(
            return_value=True
        )
        maintainer.git.is_latest_commit_ci_fix = MagicMock(return_value=False)
        maintainer.fix_ci_with_retries = MagicMock(return_value=True)
        should_continue, ci_was_passing = maintainer._check_and_fix_pre_existing_ci()
        assert should_continue is True
        assert ci_was_passing is True
        maintainer.fix_ci_with_retries.assert_called_once()


class TestDryRunGuards:
    """The AI fix paths must not run in dry-run or when a fix cannot land."""

    def test_ask_ai_to_fix_skips_agent(self, maintainer):
        maintainer.agent.ask = MagicMock()
        assert maintainer._ask_ai_to_fix("fix it", {"logs": "boom"}) is None
        maintainer.agent.ask.assert_not_called()

    def test_pre_existing_ci_failure_not_fixed_in_dry_run(self, maintainer):
        maintainer.github.get_ci_conclusion = MagicMock(return_value="failure")
        maintainer.git.is_latest_commit_from_maintainer = MagicMock(return_value=True)
        maintainer.fix_ci_with_retries = MagicMock()
        should_continue, _ = maintainer._check_and_fix_pre_existing_ci()
        assert should_continue is True
        maintainer.fix_ci_with_retries.assert_not_called()

    def test_fix_ci_skipped_when_push_disabled(self, repo_path, default_config):
        # A fix cannot reach CI without a push, so none should be attempted
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.get_ci_failure_logs = MagicMock()
        assert maintainer.fix_ci_with_retries() is False
        maintainer.github.get_ci_failure_logs.assert_not_called()

    def test_pre_existing_ci_failure_continues_when_push_disabled(
        self, repo_path, default_config
    ):
        # push_changes=False: a pre-existing failure the tool cannot fix
        # must not fail the repo; maintenance continues without a fix attempt
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.get_ci_conclusion = MagicMock(return_value="failure")
        maintainer.git.is_latest_commit_from_maintainer = MagicMock(return_value=True)
        maintainer.fix_ci_with_retries = MagicMock()
        should_continue, ci_was_passing = maintainer._check_and_fix_pre_existing_ci()
        assert should_continue is True
        assert ci_was_passing is False
        maintainer.fix_ci_with_retries.assert_not_called()


class TestResetChangesEscalation:
    """A failed checkout must escalate to a hard reset before giving up."""

    def _git(self, repo_path, checkout_ok, hard_ok=True):
        git = gm.GitClient(repo_path, MagicMock())
        calls = []

        def fake_run(args):
            calls.append(args)
            if args[:3] == ["git", "checkout", "--"]:
                return checkout_ok, "", "boom"
            if args[:3] == ["git", "reset", "--hard"]:
                return hard_ok, "", "boom"
            return True, "", ""

        git._run = fake_run
        return git, calls

    def test_hard_reset_recovers_a_failed_checkout(self, repo_path):
        git, calls = self._git(repo_path, checkout_ok=False)
        assert git.reset_changes() is True
        assert ["git", "reset", "--hard", "HEAD"] in calls
        assert ["git", "clean", "-ffd"] in calls

    def test_gives_up_when_the_hard_reset_also_fails(self, repo_path):
        git, calls = self._git(repo_path, checkout_ok=False, hard_ok=False)
        assert git.reset_changes() is False
        assert ["git", "clean", "-ffd"] not in calls

    def test_no_hard_reset_when_checkout_succeeds(self, repo_path):
        git, calls = self._git(repo_path, checkout_ok=True)
        assert git.reset_changes() is True
        assert ["git", "reset", "--hard", "HEAD"] not in calls

    def test_clean_keeps_ignored_build_state(self, repo_path):
        # -x would delete .venv / node_modules the project needs
        git, calls = self._git(repo_path, checkout_ok=True)
        git.reset_changes()
        assert not any("-ffdx" in arg for call in calls for arg in call)


class TestFixRefusesDirtyTree:
    """The CI fix path commits the whole tree, so it must not run over one
    that an earlier stage failed to discard."""

    def _maintainer(self, repo_path, default_config):
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=True
        )
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        return maintainer

    def test_fix_ci_failure_refuses(self, repo_path, default_config):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer._ask_ai_to_fix = MagicMock()
        assert maintainer.fix_ci_failure("boom") is False
        maintainer._ask_ai_to_fix.assert_not_called()

    def test_fix_ci_with_retries_refuses_before_fetching_logs(
        self, repo_path, default_config
    ):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.github.get_ci_failure_logs = MagicMock()
        assert maintainer.fix_ci_with_retries() is False
        maintainer.github.get_ci_failure_logs.assert_not_called()

    def test_clean_tree_is_allowed(self, repo_path, default_config):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.git.is_workdir_clean = MagicMock(return_value=True)
        maintainer._ask_ai_to_fix = MagicMock(return_value=None)
        maintainer.git.reset_changes = MagicMock(return_value=True)
        assert maintainer.fix_ci_failure("boom") is False
        maintainer._ask_ai_to_fix.assert_called_once()


class TestGitClientRollback:
    """rollback_to force pushes, so it must not run on a failed reset."""

    def test_rollback_resets_then_force_pushes(self, repo_path, monkeypatch):
        git = gm.GitClient(repo_path, MagicMock())
        git.reset_hard = MagicMock(return_value=(True, "", ""))
        pushed = []
        monkeypatch.setattr(
            gm,
            "run_git",
            lambda args, cwd, env_runner=None, timeout=None: (
                pushed.append(args) or (True, "", "")
            ),
        )
        assert git.rollback_to("deadbeefcafe") is True
        git.reset_hard.assert_called_once_with("deadbeefcafe")
        assert pushed == [["push", "--force"]]

    def test_rollback_does_not_push_when_reset_fails(self, repo_path, monkeypatch):
        git = gm.GitClient(repo_path, MagicMock())
        git.reset_hard = MagicMock(return_value=(False, "", "no such commit"))
        pushed = []
        monkeypatch.setattr(
            gm,
            "run_git",
            lambda *a, **k: pushed.append(a) or (True, "", ""),
        )
        assert git.rollback_to("deadbeefcafe") is False
        assert pushed == []


class TestWritability:
    """Writability comes from the API, with the push probe as a fallback."""

    def _client(self, tmp_path, result, remote="git@github.com:owner/repo.git"):
        git = MagicMock()
        git.get_remote_url.return_value = remote
        client = gm.GitHubClient(tmp_path, git, MagicMock())
        client.is_authenticated = True
        client._run = MagicMock(return_value=result)
        return client

    def _view(self, **fields):
        return True, json.dumps(fields), ""

    def test_non_github_remote_is_undetermined(self, tmp_path):
        # "owner/name" from a GitLab URL would otherwise be answered by the
        # unrelated github.com repository of that name
        client = self._client(
            tmp_path,
            self._view(isArchived=False, viewerPermission="WRITE"),
            remote="https://gitlab.com/owner/repo.git",
        )
        assert client.get_write_access() is None
        client._run.assert_not_called()

    def test_repo_is_named_explicitly(self, tmp_path):
        # gh prefers an `upstream` remote when resolving a base repo, so a
        # fork would otherwise report the parent's READ permission
        client = self._client(
            tmp_path, self._view(isArchived=False, viewerPermission="WRITE")
        )
        client.get_write_access()
        assert "owner/repo" in client._run.call_args[0][0]

    def test_write_permission_grants_access(self, tmp_path):
        client = self._client(
            tmp_path, self._view(isArchived=False, viewerPermission="WRITE")
        )
        assert client.get_write_access() is True

    def test_archived_repo_is_not_writable(self, tmp_path):
        client = self._client(
            tmp_path, self._view(isArchived=True, viewerPermission="ADMIN")
        )
        assert client.get_write_access() is False

    def test_read_permission_is_not_writable(self, tmp_path):
        client = self._client(
            tmp_path, self._view(isArchived=False, viewerPermission="READ")
        )
        assert client.get_write_access() is False

    def test_api_failure_is_undetermined(self, tmp_path):
        client = self._client(tmp_path, (False, "", "boom"))
        assert client.get_write_access() is None

    def test_missing_permission_is_undetermined(self, tmp_path):
        client = self._client(tmp_path, self._view(isArchived=False))
        assert client.get_write_access() is None

    def test_falls_back_to_push_probe_when_undetermined(
        self, repo_path, default_config
    ):
        maintainer = make_maintainer(repo_path, default_config)
        maintainer.github.get_write_access = MagicMock(return_value=None)
        maintainer.git.is_writable = MagicMock(return_value=True)
        assert maintainer._is_writable() is True
        maintainer.git.is_writable.assert_called_once()

    def test_api_answer_skips_the_push_probe(self, repo_path, default_config):
        # The push probe fires pre-push hooks, so it must not run needlessly
        maintainer = make_maintainer(repo_path, default_config)
        maintainer.github.get_write_access = MagicMock(return_value=False)
        maintainer.git.is_writable = MagicMock(return_value=True)
        assert maintainer._is_writable() is False
        maintainer.git.is_writable.assert_not_called()


class TestFailedResetAborts:
    """A working tree that cannot be cleaned must abort the repository."""

    def test_reset_failure_raises(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config)
        maintainer.git.reset_changes = MagicMock(return_value=False)
        with pytest.raises(gm.WorkingTreeError):
            maintainer._reset_changes()

    def test_unreported_changes_that_cannot_be_reset_abort(
        self, repo_path, default_config
    ):
        (repo_path / "package.json").write_text("{}")
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.agent.ask_json = MagicMock(
            return_value={"updated": False, "reasoning": "none needed"}
        )
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        maintainer.git.reset_changes = MagicMock(return_value=False)
        with pytest.raises(gm.WorkingTreeError):
            maintainer.update_dependencies()

    def test_maintain_reports_failure_instead_of_raising(
        self, repo_path, default_config
    ):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer._validate_repo = MagicMock(return_value=True)
        maintainer._check_and_fix_pre_existing_ci = MagicMock(
            return_value=(True, True)
        )
        maintainer.merge_dependabot_prs = MagicMock(return_value=[])
        maintainer.update_dependencies = MagicMock(
            side_effect=gm.WorkingTreeError("boom")
        )
        assert maintainer.maintain() == (gm.STATUS_FAILED, False)

    def test_stash_is_kept_when_the_tree_cannot_be_cleaned(
        self, repo_path, default_config
    ):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        maintainer.git.get_stash_ref = MagicMock(side_effect=[None, "ours"])
        maintainer.git.stash_push = MagicMock(return_value=(True, "", ""))
        maintainer.git.stash_apply = MagicMock(return_value=(True, "", ""))
        maintainer.git.stash_drop = MagicMock(return_value=(True, "", ""))
        maintainer.git.reset_changes = MagicMock(return_value=False)
        maintainer._try_fix_tests = MagicMock(return_value=False)
        with pytest.raises(gm.WorkingTreeError):
            maintainer.fix_test_with_retries("boom")
        # The stash holds the only clean copy of the dependency updates left
        maintainer.git.stash_drop.assert_not_called()


class TestAgentClientAsk:
    """The agent subprocess must run inside the repository it is maintaining."""

    class _FakeProc:
        pid = 1234
        returncode = 0

        def communicate(self, timeout=None):
            return '{"ok": true}', ""

    def test_ask_runs_agent_in_the_repo(self, default_config, monkeypatch, tmp_path):
        client = gm.AgentClient(tmp_path, "test-repo", default_config, MagicMock())
        captured = {}

        def fake_popen(cmd, **kwargs):
            captured["kwargs"] = kwargs
            return self._FakeProc()

        monkeypatch.setattr(gm.subprocess, "Popen", fake_popen)
        assert client.ask("do something") == '{"ok": true}'
        assert captured["kwargs"]["cwd"] == tmp_path


class TestFixTestWithRetriesStash:
    """Dependency updates must survive fix attempts without touching other stashes."""

    def _maintainer(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        maintainer.git.stash_push = MagicMock(return_value=(True, "", ""))
        maintainer.git.stash_apply = MagicMock(return_value=(True, "", ""))
        maintainer.git.stash_drop = MagicMock(return_value=(True, "", ""))
        maintainer.git.reset_changes = MagicMock(return_value=True)
        return maintainer

    def test_stash_push_includes_untracked_files(self, repo_path):
        git = gm.GitClient(repo_path, MagicMock())
        git._run = MagicMock(return_value=(True, "", ""))
        git.stash_push("msg")
        assert "-u" in git._run.call_args[0][0]

    def test_aborts_when_no_stash_entry_was_created(self, repo_path, default_config):
        maintainer = self._maintainer(repo_path, default_config)
        # `git stash push` exits 0 without creating an entry when it has
        # nothing to save; refs/stash still points at an unrelated stash
        maintainer.git.get_stash_ref = MagicMock(return_value="preexisting")
        maintainer._try_fix_tests = MagicMock(return_value=True)
        assert maintainer.fix_test_with_retries("boom") is False
        maintainer.git.stash_apply.assert_not_called()
        maintainer.git.stash_drop.assert_not_called()
        maintainer._try_fix_tests.assert_not_called()

    def test_mid_loop_apply_failure_stops_instead_of_testing_wrong_tree(
        self, repo_path, default_config
    ):
        # An unrestored tree would make run_tests describe a state the next
        # attempt is not fixing, and stash apply -u aborts on existing paths
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.git.get_stash_ref = MagicMock(side_effect=[None, "ours"])
        maintainer.git.stash_apply = MagicMock(
            side_effect=[(True, "", ""), (False, "", "exists")]
        )
        maintainer._try_fix_tests = MagicMock(return_value=False)
        maintainer.run_tests = MagicMock()
        assert maintainer.fix_test_with_retries("boom") is False
        maintainer.run_tests.assert_not_called()
        # The reset leaves the stash holding the only copy of the updates
        maintainer.git.stash_drop.assert_not_called()

    def test_applies_and_drops_only_its_own_stash(self, repo_path, default_config):
        maintainer = self._maintainer(repo_path, default_config)
        maintainer.git.get_stash_ref = MagicMock(side_effect=["preexisting", "ours"])
        maintainer._try_fix_tests = MagicMock(return_value=True)
        assert maintainer.fix_test_with_retries("boom") is True
        maintainer.git.stash_apply.assert_called_once()
        maintainer.git.stash_drop.assert_called_once()


class TestCommitAndPushWithoutPush:
    """--no-push still commits: uncommitted changes skip the repo on later runs."""

    def test_commits_locally_without_pushing(
        self, repo_path, default_config, monkeypatch
    ):
        maintainer = make_maintainer(
            repo_path, default_config, dry_run=False, push_changes=False
        )
        maintainer.git.is_workdir_clean = MagicMock(return_value=False)
        maintainer.git.stage_all = MagicMock(return_value=(True, "", ""))
        maintainer.git.has_staged_changes = MagicMock(return_value=True)
        ran = []

        def fake_run_git(args, cwd, env_runner=None, timeout=None):
            ran.append(args)
            return True, "", ""

        monkeypatch.setattr(gm, "run_git", fake_run_git)
        assert maintainer.commit_and_push("msg") == (True, True)
        assert ran == [["commit", "-m", "msg"]]


class TestBuildCommitMessage:
    """Tests for commit message building."""

    def test_commit_message_deps_only(self, maintainer):
        msg = maintainer.build_commit_message(had_dep_updates=True)
        assert "chore(deps): update direct dependencies" in msg

    def test_commit_message_no_changes(self, maintainer):
        msg = maintainer.build_commit_message(had_dep_updates=False)
        assert "chore: automated maintenance" in msg
        assert "\n\n\n" not in msg

    def test_commit_message_fix(self, maintainer):
        msg = maintainer.build_commit_message(had_dep_updates=False, is_fix=True)
        assert "fix: CI build failure" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
