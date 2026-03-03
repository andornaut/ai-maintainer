#!/usr/bin/env python3
"""Unit tests for github-maintainer."""

import importlib.machinery

# Import the module (it's an executable without .py extension)
import importlib.util
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

module_path = Path(__file__).parent / "github-maintainer"
loader = importlib.machinery.SourceFileLoader("github_maintainer", str(module_path))
spec = importlib.util.spec_from_loader("github_maintainer", loader)
gm = importlib.util.module_from_spec(spec)
sys.modules["github_maintainer"] = gm
spec.loader.exec_module(gm)


@pytest.fixture
def default_config():
    """Create a default Config for testing."""
    return gm.Config(
        agent_command="echo",
        agent_flags="",
        auto_merge_dependabot=True,
        auto_update_dependencies=True,
        dependency_min_age_days=30,
        dry_run=True,
        exclude=set(),
        max_fix_attempts=4,
        push_changes=False,
        rollback_on_ci_failure=False,
        run_tests=True,
    )


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

    def test_no_version_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            env = gm.ProjectEnvironment(Path(tmpdir))
            assert env.env_runner is None
            assert env.needs_bash is False

    def test_nvmrc_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".nvmrc").write_text("18.0.0")
            env = gm.ProjectEnvironment(tmppath)
            # Will be None if fnm/nvm not installed, which is fine for testing
            # The important thing is it doesn't crash

    def test_pipfile_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "Pipfile").write_text("[packages]")
            env = gm.ProjectEnvironment(tmppath)
            assert env.env_runner == "pipenv run"
            assert env.needs_bash is False

    def test_poetry_detection(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / "poetry.lock").write_text("")
            env = gm.ProjectEnvironment(tmppath)
            assert env.env_runner == "poetry run"
            assert env.needs_bash is False


class TestAgentClientJsonExtraction:
    """Tests for AgentClient JSON extraction."""

    def test_extract_plain_json(self, default_config):
        client = gm.AgentClient(Path("/tmp"), "test-repo", default_config, MagicMock())
        result = client._extract_json_from_response('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_extract_json_from_markdown_block(self, default_config):
        client = gm.AgentClient(Path("/tmp"), "test-repo", default_config, MagicMock())
        response = """Here's the result:
```json
{"should_update": true, "commands": ["npm update"]}
```
"""
        result = client._extract_json_from_response(response)
        assert result == '{"should_update": true, "commands": ["npm update"]}'

    def test_extract_json_from_code_block_without_lang(self, default_config):
        client = gm.AgentClient(Path("/tmp"), "test-repo", default_config, MagicMock())
        response = """```
{"fixed": false}
```"""
        result = client._extract_json_from_response(response)
        assert result == '{"fixed": false}'


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
        success, stdout, stderr = gm.run_command(
            "echo hello && echo world", Path("/tmp"), shell=True
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
            auto_merge_dependabot=True,
            auto_update_dependencies=True,
            dependency_min_age_days=30,
            dry_run=False,
            exclude={"excluded-repo"},
            max_fix_attempts=4,
            push_changes=True,
            rollback_on_ci_failure=False,
            run_tests=True,
        )
        assert config.agent_command == "claude"
        assert config.dependency_min_age_days == 30
        assert "excluded-repo" in config.exclude


class TestGitClient:
    """Tests for GitClient class."""

    def test_is_git_repo_true(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            client = gm.GitClient(tmppath, MagicMock())
            assert client.is_git_repo() is True

    def test_is_git_repo_false(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            client = gm.GitClient(Path(tmpdir), MagicMock())
            assert client.is_git_repo() is False

    def test_repo_name(self):
        client = gm.GitClient(Path("/path/to/my-repo"), MagicMock())
        assert client.repo_name == "my-repo"


class TestMaintainerValidation:
    """Tests for Maintainer validation methods."""

    def test_is_valid_dependabot_pr_valid_branch(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            maintainer.github.is_commit_verified = MagicMock(return_value=True)

            pr = {
                "number": 123,
                "headRefName": "dependabot/npm_and_yarn/lodash-4.17.21",
                "headRefOid": "abc123",
            }
            assert maintainer._is_valid_dependabot_pr(pr) is True

    def test_is_valid_dependabot_pr_invalid_branch(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)

            pr = {
                "number": 123,
                "headRefName": "feature/some-feature",
                "headRefOid": "abc123",
            }
            assert maintainer._is_valid_dependabot_pr(pr) is False

    def test_is_valid_dependabot_pr_unverified_commit(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            maintainer.github.is_commit_verified = MagicMock(return_value=False)

            pr = {
                "number": 123,
                "headRefName": "dependabot/npm_and_yarn/lodash-4.17.21",
                "headRefOid": "abc123",
            }
            assert maintainer._is_valid_dependabot_pr(pr) is False


class TestDetectTestCommand:
    """Tests for test command detection."""

    def test_detect_npm_test(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            (tmppath / "package.json").write_text('{"scripts": {"test": "jest"}}')
            maintainer = gm.Maintainer(tmppath, default_config)
            assert maintainer.detect_test_command() == "npm test"

    def test_detect_pytest(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            (tmppath / "pyproject.toml").write_text("[tool.pytest]")
            (tmppath / "tests").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            assert maintainer.detect_test_command() == "pytest"

    def test_detect_cargo_test(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            (tmppath / "Cargo.toml").write_text("[package]")
            maintainer = gm.Maintainer(tmppath, default_config)
            assert maintainer.detect_test_command() == "cargo test"

    def test_detect_no_test(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            assert maintainer.detect_test_command() is None


class TestBuildCommitMessage:
    """Tests for commit message building."""

    def test_commit_message_deps_only(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            msg = maintainer.build_commit_message([], had_dep_updates=True)
            assert "chore(deps): update direct dependencies" in msg

    def test_commit_message_prs_only(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            msg = maintainer.build_commit_message([123], had_dep_updates=False)
            assert "#123" in msg
            assert "dependabot" in msg.lower()

    def test_commit_message_fix(self, default_config):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            (tmppath / ".git").mkdir()
            maintainer = gm.Maintainer(tmppath, default_config)
            msg = maintainer.build_commit_message([], had_dep_updates=False, is_fix=True)
            assert "fix: CI build failure" in msg


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
