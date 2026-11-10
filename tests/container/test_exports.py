import os
import shlex
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


def _run(checkout: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    executable = shutil.which(arguments[0])
    if executable is None:
        raise RuntimeError(f"Required container tool is missing: {arguments[0]}")
    return subprocess.run(  # noqa: S603
        [executable, *arguments[1:]],
        cwd=checkout,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )


def _git(checkout: Path, *arguments: str) -> None:
    _run(checkout, ["git", *arguments]).check_returncode()


def _publish(checkout: Path, scratch: Path, outputs: list[str]) -> subprocess.CompletedProcess[str]:
    source = Path(__file__).with_name("run").read_text()
    exclusions = source[source.index("excluded() {") : source.index("contained() {")]
    publication = source[source.index("publish_outputs() (") :].split("\n)\n", 1)[0] + "\n)\n"
    program = (
        "set -euo pipefail\n"
        "checkout=$1\nscratch=$2\nmarker_path=$scratch/inactive-marker\nshift 2\n"
        "git_metadata=(git --no-optional-locks -c core.fsmonitor=false -c core.hooksPath=/dev/null)\n"
        'outputs=("$@")\n' + exclusions + publication + "\npublish_outputs\n"
    )
    return _run(checkout, ["bash", "-c", program, "export-test", str(checkout), str(scratch), *outputs])


@pytest.fixture
def export_context(tmp_path: Path) -> tuple[Path, Path]:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    scratch = tmp_path / "scratch"
    (scratch / "outputs").mkdir(parents=True)
    (checkout / ".gitignore").write_text("dist/\n")
    (checkout / "source.txt").write_text("source must remain\n")
    _git(checkout, "init", "--quiet")
    _git(checkout, "config", "user.name", "Fixture")
    _git(checkout, "config", "user.email", "fixture@example.invalid")
    _git(checkout, "add", ".gitignore", "source.txt")
    _git(checkout, "commit", "--quiet", "--message", "fixture")
    return checkout, scratch


def _trees(checkout: Path, scratch: Path, output: str = "dist") -> tuple[Path, Path]:
    previous = checkout / output
    previous.mkdir()
    (previous / "old.js.map").write_text("stale generated map\n")
    fresh = scratch / "outputs" / output
    fresh.mkdir()
    (fresh / "main.js").write_text("production code\n")
    return previous, fresh


class TestPublishOutputs:
    def test_replaces_generated_tree_and_preserves_source(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        previous, _ = _trees(checkout, scratch)
        (checkout / "sibling.txt").write_text("sibling must remain\n")

        result = _publish(checkout, scratch, ["dist"])

        assert result.returncode == 0, result.stderr
        assert not (previous / "old.js.map").exists()
        assert (previous / "main.js").read_text() == "production code\n"
        assert (checkout / "source.txt").read_text() == "source must remain\n"
        assert (checkout / "sibling.txt").read_text() == "sibling must remain\n"
        assert not list(checkout.glob(".check-output.*"))

    def test_first_build_accepts_an_ignored_missing_directory(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        fresh = scratch / "outputs" / "dist"
        fresh.mkdir()
        (fresh / "main.js").write_text("production code\n")

        result = _publish(checkout, scratch, ["dist"])

        assert result.returncode == 0, result.stderr
        assert (checkout / "dist" / "main.js").read_text() == "production code\n"

    def test_validates_every_output_before_replacing_any(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        previous, _ = _trees(checkout, scratch)
        _trees(checkout, scratch, "unignored")

        result = _publish(checkout, scratch, ["dist", "unignored"])

        assert result.returncode != 0
        assert (previous / "old.js.map").read_text() == "stale generated map\n"
        assert not (previous / "main.js").exists()
        assert not list(checkout.glob(".check-output.*"))

    def test_restores_previous_output_if_publication_fails(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        previous, _ = _trees(checkout, scratch)
        executable = shutil.which("mv")
        assert executable is not None
        tools = scratch / "tools"
        tools.mkdir()
        wrapper = tools / "mv"
        wrapper.write_text(f'#!/bin/sh\ncase "$3" in */new) exit 1 ;; *) exec {executable} "$@" ;; esac\n')
        wrapper.chmod(0o700)
        monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")

        result = _publish(checkout, scratch, ["dist"])

        assert result.returncode != 0
        assert (previous / "old.js.map").read_text() == "stale generated map\n"
        assert not (previous / "main.js").exists()
        assert not list(checkout.glob(".check-output.*"))

    def test_replaces_an_explicit_ignored_file(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        (checkout / ".gitignore").write_text("dist/\nartifact.bin\n")
        (checkout / "artifact.bin").write_text("old generated file\n")
        (scratch / "outputs" / "artifact.bin").write_text("new generated file\n")

        result = _publish(checkout, scratch, ["artifact.bin"])

        assert result.returncode == 0, result.stderr
        assert (checkout / "artifact.bin").read_text() == "new generated file\n"

    def test_refuses_a_tracked_source_file(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        (scratch / "outputs" / "source.txt").write_text("must not replace source\n")

        result = _publish(checkout, scratch, ["source.txt"])

        assert result.returncode != 0
        assert "tracked source" in result.stderr
        assert (checkout / "source.txt").read_text() == "source must remain\n"

    def test_refuses_a_tracked_descendant_even_when_staged_for_removal(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        previous, _ = _trees(checkout, scratch, "tracked-output")
        _git(checkout, "add", "tracked-output/old.js.map")
        _git(checkout, "commit", "--quiet", "--message", "track source")
        (checkout / ".gitignore").write_text("dist/\ntracked-output/\n")
        _git(checkout, "rm", "--cached", "tracked-output/old.js.map")

        result = _publish(checkout, scratch, ["tracked-output"])

        assert result.returncode != 0
        assert "tracked source" in result.stderr
        assert (previous / "old.js.map").read_text() == "stale generated map\n"

    def test_refuses_an_unignored_directory(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        previous, _ = _trees(checkout, scratch, "unignored")

        result = _publish(checkout, scratch, ["unignored"])

        assert result.returncode != 0
        assert "Git-ignored generated content" in result.stderr
        assert (previous / "old.js.map").read_text() == "stale generated map\n"

    @pytest.mark.parametrize("location", ["root", "nested"])
    def test_refuses_symlinks_and_preserves_their_target(
        self, export_context: tuple[Path, Path], location: str
    ) -> None:
        checkout, scratch = export_context
        previous, _ = _trees(checkout, scratch)
        outside = checkout.parent / "outside"
        outside.mkdir()
        (outside / "keep.txt").write_text("outside must remain\n")
        if location == "root":
            (previous / "old.js.map").unlink()
            previous.rmdir()
            previous.symlink_to(outside, target_is_directory=True)
        else:
            (previous / "alias").symlink_to(outside, target_is_directory=True)

        result = _publish(checkout, scratch, ["dist"])

        assert result.returncode != 0
        assert (outside / "keep.txt").read_text() == "outside must remain\n"
        assert (previous if location == "root" else previous / "alias").is_symlink()

    @pytest.mark.parametrize("location", ["previous", "fresh"])
    def test_refuses_credential_shaped_entries_without_reading_values(
        self, export_context: tuple[Path, Path], location: str
    ) -> None:
        checkout, scratch = export_context
        previous, fresh = _trees(checkout, scratch)
        (previous if location == "previous" else fresh).joinpath("credentials").mkdir()

        result = _publish(checkout, scratch, ["dist"])

        assert result.returncode != 0
        assert "excluded entry" in result.stderr
        assert (previous / "old.js.map").read_text() == "stale generated map\n"


class TestSourceSnapshots:
    def test_sanitized_archive_is_readable_under_private_umask(self, export_context: tuple[Path, Path]) -> None:
        checkout, scratch = export_context
        source = Path(__file__).with_name("run").read_text()
        functions = source[source.index("excluded() {") : source.index("\nadmission_path=\n")]
        program = (
            "set -euo pipefail\numask 077\ncheckout=$1\nscratch=$2\n"
            "git_metadata=(git --no-optional-locks -c core.fsmonitor=false -c core.hooksPath=/dev/null)\n"
            + functions
            + '\nsnapshot "$scratch/public.tar"\n'
        )

        result = _run(checkout, ["bash", "-c", program, "snapshot-test", str(checkout), str(scratch)])

        assert result.returncode == 0, result.stderr
        assert stat.S_IMODE((scratch / "public.tar").stat().st_mode) == 0o644


def _launcher_fixture(monkeypatch: pytest.MonkeyPatch, checkout: Path, scratch: Path) -> tuple[Path, Path, Path]:
    directory = checkout / "tests" / "container"
    directory.mkdir(parents=True)
    launcher = directory / "run"
    shutil.copyfile(Path(__file__).with_name("run"), launcher)
    launcher.chmod(0o700)
    (directory / "Dockerfile").write_text("FROM scratch\n")
    (directory / "check").write_text("#!/bin/sh\nexit 0\n")
    (directory / "check").chmod(0o700)
    (checkout / "source.txt").chmod(0o600)
    (directory / "context").write_text("source.txt\ntests/container/Dockerfile\ntests/container/check\n")
    log = scratch / "phases.log"
    captured = scratch / "context.tar"
    reports = scratch / "reports"
    reports.mkdir()
    (reports / "coverage.txt").write_text("fresh coverage\n")
    quoted_log = shlex.quote(str(log))
    (directory / "admit").write_text(
        f'#!/bin/sh\nprintf "%s\\n" "admit:$1" >> {quoted_log}\nprintf "%s\\n" "probe output"\n'
    )
    (directory / "admit").chmod(0o700)
    tools = scratch / "tools"
    tools.mkdir()
    (tools / "docker").write_text(
        '#!/bin/sh\nset -eu\ncase "$1" in\n'
        'build)\nif [ "${2:-}" = --help ]; then printf "%s\\n" --resource --ulimit; exit 0; fi\n'
        f'printf "%s\\n" docker-build >> {quoted_log}\n'
        'while [ "$#" -gt 0 ]; do\nif [ "$1" = --iidfile ]; then shift; '
        f'printf "%s\\n" "sha256:{"1" * 64}" > "$1"; fi\nshift\ndone\n'
        f"cat > {shlex.quote(str(captured))}\n;;\n"
        f'run)\nprintf "%s\\n" docker-run >> {quoted_log}\nexporting=0\n'
        'while [ "$#" -gt 0 ]; do\nif [ "$1" = --cidfile ]; then shift; '
        f'printf "%s\\n" "{"1" * 64}" > "$1"; fi\n'
        f'if [ "$1" = --tmpfs ]; then shift; printf "%s\\n" "$1" >> {shlex.quote(str(scratch / "mounts.log"))}; fi\n'
        'if [ "$1" = CHECK_EXPORT=1 ]; then exporting=1; fi\nshift\ndone\n'
        f'if [ "$exporting" = 1 ]; then tar --create --file - --directory {shlex.quote(str(reports))} coverage.txt; '
        'else printf "%s\\n" "command output"; fi\n;;\n'
        f'rm) printf "%s\\n" docker-rm >> {quoted_log} ;;\n'
        "ps) ;;\n*) exit 2 ;;\nesac\n"
    )
    (tools / "docker").chmod(0o700)
    monkeypatch.setenv("PATH", f"{tools}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("CHECK_TASKS", "lint test build")
    monkeypatch.setenv("CHECK_ADMISSION", "tests/container/admit")
    monkeypatch.setenv("COMMAND", "unused fixture command")
    return launcher, log, captured


class TestLaunchContracts:
    def test_snapshot_workspace_is_owned_by_the_selected_runtime_identity(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, _, _ = _launcher_fixture(monkeypatch, checkout, scratch)

        result = _run(checkout, [str(launcher), "test"])

        assert result.returncode == 0, result.stderr
        workspace = next(
            line for line in (scratch / "mounts.log").read_text().splitlines() if line.startswith("/work:")
        )
        assert ",uid=19001,gid=19001," in workspace

    def test_public_test_exports_declared_reports_with_replacement(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, _, _ = _launcher_fixture(monkeypatch, checkout, scratch)
        monkeypatch.setenv("TEST_OUTPUTS", "coverage.txt")
        (checkout / ".gitignore").write_text("dist/\ncoverage.txt\n")
        (checkout / "coverage.txt").write_text("old coverage\n")

        result = _run(checkout, [str(launcher), "test"])

        assert result.returncode == 0, result.stderr
        assert (checkout / "coverage.txt").read_text() == "fresh coverage\n"

    def test_aggregate_check_does_not_export_test_reports(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, _, _ = _launcher_fixture(monkeypatch, checkout, scratch)
        monkeypatch.setenv("TEST_OUTPUTS", "coverage.txt")
        (checkout / ".gitignore").write_text("dist/\ncoverage.txt\n")
        (checkout / "coverage.txt").write_text("old coverage\n")

        result = _run(checkout, [str(launcher), "check"])

        assert result.returncode == 0, result.stderr
        assert (checkout / "coverage.txt").read_text() == "old coverage\n"

    def test_context_headers_are_readable_without_changing_source_permissions(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, _, captured = _launcher_fixture(monkeypatch, checkout, scratch)

        result = _run(checkout, ["bash", "-c", 'umask 077; exec "$1" test', "fixture", str(launcher)])

        assert result.returncode == 0, result.stderr
        archive = _run(checkout, ["tar", "--list", "--verbose", "--file", str(captured)])
        assert archive.returncode == 0
        assert "-rw-r--r--" in archive.stdout
        assert "-rwxr-xr-x" in archive.stdout
        assert stat.S_IMODE((checkout / "source.txt").stat().st_mode) == 0o600
        assert stat.S_IMODE((checkout / "tests" / "container" / "check").stat().st_mode) == 0o700

    def test_admission_runs_before_image_and_every_declared_category(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, log, _ = _launcher_fixture(monkeypatch, checkout, scratch)

        result = _run(checkout, [str(launcher), "check"])

        assert result.returncode == 0, result.stderr
        assert log.read_text().splitlines() == [
            "admit:build-image",
            "docker-build",
            "admit:lint",
            "docker-run",
            "docker-rm",
            "admit:test",
            "docker-run",
            "docker-rm",
            "admit:build",
            "docker-run",
            "docker-rm",
        ]

    def test_admission_output_does_not_pollute_exec_stdout(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, log, _ = _launcher_fixture(monkeypatch, checkout, scratch)

        result = _run(checkout, [str(launcher), "exec"])

        assert result.returncode == 0, result.stderr
        assert result.stdout == "command output\n"
        assert log.read_text().splitlines() == [
            "admit:build-image",
            "docker-build",
            "admit:exec",
            "docker-run",
            "docker-rm",
        ]

    @pytest.mark.parametrize("phase", ["build-image", "test"])
    def test_admission_denial_does_not_start_the_denied_phase(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, phase: str
    ) -> None:
        checkout, scratch = export_context
        launcher, log, _ = _launcher_fixture(monkeypatch, checkout, scratch)
        admission = checkout / "tests" / "container" / "admit"
        admission.write_text(admission.read_text() + f'if [ "$1" = {shlex.quote(phase)} ]; then exit 13; fi\n')

        result = _run(checkout, [str(launcher), "check"])

        assert result.returncode == 13
        events = log.read_text().splitlines()
        assert events[-1] == f"admit:{phase}"
        assert events.count("docker-run") == (0 if phase == "build-image" else 1)

    @pytest.mark.parametrize("invalid", ["outside", "symlink", "not executable"])
    def test_admission_requires_a_literal_regular_executable_inside_the_checkout(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch, invalid: str
    ) -> None:
        checkout, scratch = export_context
        launcher, log, captured = _launcher_fixture(monkeypatch, checkout, scratch)
        admission = checkout / "tests" / "container" / "admit"
        outside = checkout.parent / "outside-admit"
        outside.write_text(admission.read_text())
        outside.chmod(0o700)
        if invalid == "outside":
            monkeypatch.setenv("CHECK_ADMISSION", "../outside-admit")
        elif invalid == "symlink":
            admission.unlink()
            admission.symlink_to(outside)
        else:
            admission.chmod(0o600)

        result = _run(checkout, [str(launcher), "test"])

        assert result.returncode != 0
        assert "admission" in result.stderr
        assert not log.exists()
        assert not captured.exists()

    def test_default_has_no_admission_gate(
        self, export_context: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        checkout, scratch = export_context
        launcher, log, _ = _launcher_fixture(monkeypatch, checkout, scratch)
        monkeypatch.delenv("CHECK_ADMISSION")

        result = _run(checkout, [str(launcher), "test"])

        assert result.returncode == 0, result.stderr
        assert log.read_text().splitlines() == ["docker-build", "docker-run", "docker-rm"]
