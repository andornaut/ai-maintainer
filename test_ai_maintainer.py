#!/usr/bin/env python3
"""Unit tests for ai-maintainer."""

import importlib.machinery

# Import the module (it's an executable without .py extension)
import importlib.util
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


class TestMergePrsOnGithub:
    """Tests for _merge_prs_on_github method."""

    def test_dry_run_skips_merge(self, maintainer):
        success, merged = maintainer._merge_prs_on_github([1, 2])
        assert success is True
        assert merged == [1, 2]

    def test_merge_success(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(return_value=(True, ""))
        success, merged = maintainer._merge_prs_on_github([1, 2])
        assert success is True
        assert merged == [1, 2]
        assert maintainer.github.merge_pr.call_count == 2

    def test_merge_partial_failure(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            side_effect=[(True, ""), (False, "merge blocked"), (True, "")]
        )
        success, merged = maintainer._merge_prs_on_github([1, 2, 3])
        assert success is True
        assert merged == [1, 3]

    def test_empty_list(self, maintainer):
        success, merged = maintainer._merge_prs_on_github([])
        assert success is True
        assert merged == []

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
        success, merged = maintainer._merge_prs_on_github([5])
        assert success is True
        assert merged == []
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
        success, merged = maintainer._merge_prs_on_github([5])
        assert success is True
        assert merged == []
        maintainer.github.comment_pr.assert_not_called()

    def test_already_merged_counts_as_merged(self, repo_path, default_config):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            return_value=(True, "! Pull request #9 was already merged")
        )
        maintainer.github.comment_pr = MagicMock(return_value=True)
        success, merged = maintainer._merge_prs_on_github([9])
        assert success is True
        assert merged == [9]
        maintainer.github.comment_pr.assert_not_called()

    def test_non_conflict_failure_skips_without_rebase(
        self, repo_path, default_config
    ):
        maintainer = make_maintainer(repo_path, default_config, dry_run=False)
        maintainer.github.merge_pr = MagicMock(
            return_value=(False, "merge blocked by branch protection")
        )
        maintainer.github.comment_pr = MagicMock(return_value=True)
        success, merged = maintainer._merge_prs_on_github([7])
        assert success is True
        assert merged == []
        maintainer.github.comment_pr.assert_not_called()


class TestDryRunGuards:
    """The AI fix paths must not run in dry-run or when a fix cannot land."""

    def test_ask_ai_to_fix_skips_agent(self, maintainer):
        maintainer.agent.ask = MagicMock()
        assert maintainer._ask_ai_to_fix("fix it", {"logs": "boom"}) is None
        maintainer.agent.ask.assert_not_called()

    def test_pre_existing_ci_failure_not_fixed_in_dry_run(self, maintainer):
        maintainer.github.get_latest_ci_conclusion = MagicMock(return_value="failure")
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
        maintainer.github.get_latest_ci_conclusion = MagicMock(return_value="failure")
        maintainer.git.is_latest_commit_from_maintainer = MagicMock(return_value=True)
        maintainer.fix_ci_with_retries = MagicMock()
        should_continue, ci_was_passing = maintainer._check_and_fix_pre_existing_ci()
        assert should_continue is True
        assert ci_was_passing is False
        maintainer.fix_ci_with_retries.assert_not_called()


class TestBuildCommitMessage:
    """Tests for commit message building."""

    def test_commit_message_deps_only(self, maintainer):
        msg = maintainer.build_commit_message(had_dep_updates=True)
        assert "chore(deps): update direct dependencies" in msg

    def test_commit_message_no_changes(self, maintainer):
        msg = maintainer.build_commit_message(had_dep_updates=False)
        assert "chore: automated maintenance" in msg

    def test_commit_message_fix(self, maintainer):
        msg = maintainer.build_commit_message(had_dep_updates=False, is_fix=True)
        assert "fix: CI build failure" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
