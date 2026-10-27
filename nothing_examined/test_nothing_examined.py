#!/usr/bin/env python3
"""Unit tests for nothing_examined -- bare-runnable (python test_nothing_examined.py).

Both functions are pure, so every branch is reachable without any filesystem or
subprocess seam; 100% branch coverage needs no pragma.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

# The library under test sits beside this file; import it by location, not by the working
# directory, so the test runs from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import nothing_examined  # noqa: E402 - import follows the sys.path bootstrap above


class HiddenCountTest(unittest.TestCase):
    """The rule for WHEN a gate should speak: only when it examined nothing."""

    def test_examined_work_hides_nothing(self) -> None:
        self.assertEqual(nothing_examined.hidden_count(3, lambda: 99), 0)

    def test_examined_nothing_reports_the_unfiltered_count(self) -> None:
        self.assertEqual(nothing_examined.hidden_count(0, lambda: 94), 94)

    def test_examined_nothing_and_nothing_to_find_is_zero(self) -> None:
        """A genuinely empty tree hid nothing, so it must stay quiet -- this is the
        distinction the whole module exists to draw."""
        self.assertEqual(nothing_examined.hidden_count(0, lambda: 0), 0)

    def test_count_all_is_not_called_when_work_was_examined(self) -> None:
        """The common path must not pay for a second discovery walk."""
        calls: list[int] = []

        def count_all() -> int:
            calls.append(1)
            return 7

        nothing_examined.hidden_count(5, count_all)
        self.assertEqual(calls, [])

    def test_count_all_is_called_exactly_once_when_empty(self) -> None:
        calls: list[int] = []

        def count_all() -> int:
            calls.append(1)
            return 7

        self.assertEqual(nothing_examined.hidden_count(0, count_all), 7)
        self.assertEqual(calls, [1])


class NoteTest(unittest.TestCase):
    """The words themselves -- single-sourced so the gates using them cannot drift apart."""

    def test_no_note_when_nothing_hidden(self) -> None:
        self.assertIsNone(nothing_examined.note(0, noun="file", table="python_lint"))

    def test_note_names_count_noun_table_and_flag(self) -> None:
        line = nothing_examined.note(94, noun="doc", table="claim_check")
        assert line is not None  # for mypy: assertIsNotNone does not narrow
        self.assertIn("NOTHING EXAMINED", line)
        self.assertIn("all 94 candidate doc(s)", line)
        self.assertIn("[tool.claim_check] exclude", line)
        self.assertIn("--path", line)  # the default target flag
        self.assertIn("verifies nothing", line)

    def test_flag_is_overridable_for_the_two_odd_gates(self) -> None:
        """A gate may take --folder or, as shellcheck_md does, --root, so the line must
        be able to name the option the reader actually passed."""
        folder = nothing_examined.note(
            2, noun="file", table="pii_scan", flag="--folder"
        )
        root = nothing_examined.note(
            2, noun="file", table="shellcheck_md", flag="--root"
        )
        assert folder is not None and root is not None  # for mypy
        self.assertIn("under --folder", folder)
        self.assertIn("under --root", root)
        self.assertNotIn("--path", folder)

    def test_note_is_a_single_line(self) -> None:
        """Callers append it to a line list, so an embedded newline would break their
        report layout."""
        line = nothing_examined.note(1, noun="unit", table="example_gate")
        assert line is not None  # for mypy
        self.assertNotIn("\n", line)


if __name__ == "__main__":
    unittest.main()
