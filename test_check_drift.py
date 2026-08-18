#!/usr/bin/env python3
"""Unit tests for lint-configs/check-drift.py."""

import importlib.machinery

# Import the module (it lives under lint-configs/ and its name is not an identifier)
import importlib.util
import sys
from pathlib import Path

module_path = Path(__file__).parent / "lint-configs" / "check-drift.py"
loader = importlib.machinery.SourceFileLoader("check_drift", str(module_path))
spec = importlib.util.spec_from_loader("check_drift", loader)
cd = importlib.util.module_from_spec(spec)
sys.modules["check_drift"] = cd
spec.loader.exec_module(cd)

CANON = """
ignores:
  - "**/node_modules/**"
  - AGENTS.md
  - CLAUDE.md
config:
  MD013: false
"""


class TestDroppedIgnores:
    """An entry canon names is required; a repository may add to the list."""

    def test_an_identical_list_reports_nothing(self):
        assert cd.dropped_ignores(CANON, CANON) == []

    def test_an_added_entry_is_not_drift(self):
        theirs = CANON.replace("  - CLAUDE.md", '  - CLAUDE.md\n  - "fixtures/**"')
        assert cd.dropped_ignores(CANON, theirs) == []

    def test_a_dropped_entry_is_reported(self):
        theirs = CANON.replace("  - CLAUDE.md\n", "")
        assert cd.dropped_ignores(CANON, theirs) == ["CLAUDE.md"]

    def test_every_dropped_entry_is_named(self):
        theirs = "ignores:\n  - AGENTS.md\nconfig:\n  MD013: false\n"
        assert cd.dropped_ignores(CANON, theirs) == ["**/node_modules/**", "CLAUDE.md"]

    def test_a_config_with_no_ignores_key_drops_all_of_them(self):
        theirs = "config:\n  MD013: false\n"
        assert cd.dropped_ignores(CANON, theirs) == ["**/node_modules/**", "AGENTS.md", "CLAUDE.md"]

    def test_the_shipped_canon_names_the_entries_that_matter(self):
        """The list the sweep enforces, read from the file it ships rather than a fixture."""
        canon = (Path(__file__).parent / "lint-configs" / "markdown" / "markdownlint-cli2.yaml").read_text()
        assert cd.dropped_ignores(canon, "config: {}") == [
            "**/node_modules/**",
            ".agents/**",
            ".claude/**",
            "AGENTS.md",
            "CLAUDE.md",
            "GEMINI.md",
        ]
