#!/usr/bin/env python3
"""Unit tests for venv_prune -- bare-runnable (python test_venv_prune.py).

The predicate touches the filesystem, so these build real directory trees under a
temp dir rather than faking a stat seam: the whole point of the marker is that it is
a fact about the filesystem, and a faked stat would test the fake. Every branch is
reachable that way, so 100% branch coverage needs no pragma.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

# The library under test sits beside this file; import it by location, not by the working
# directory, so the test runs from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import venv_prune  # noqa: E402 - import follows the sys.path bootstrap above

_MARKER = "pyvenv.cfg"


class IsVirtualenvTest(unittest.TestCase):
    """The PEP 405 marker rule: a venv root is the directory holding pyvenv.cfg."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _make_venv(self, name: str) -> Path:
        """Create a minimal but REAL venv root: a directory holding the marker file."""
        venv = self.root / name
        venv.mkdir()
        # newline="" keeps the fixture byte-identical on every host (RL004): text mode
        # would otherwise translate "\n" to the platform line ending.
        (venv / _MARKER).write_text("home = /usr/bin\n", encoding="utf-8", newline="")
        return venv

    def test_directory_holding_the_marker_is_a_virtualenv(self) -> None:
        self.assertTrue(venv_prune.is_virtualenv(self._make_venv(".tool-env")))

    def test_the_verdict_does_not_depend_on_the_directory_name(self) -> None:
        """Name-independence is the entire reason this predicate exists: a tool's
        own venv, a hand-made one, and an oddly-named one must all prune."""
        for name in (".venv", "venv", ".tool-env", ".venv311", "env", "wibble"):
            with self.subTest(name=name):
                self.assertTrue(venv_prune.is_virtualenv(self._make_venv(name)))

    def test_plain_directory_is_not_a_virtualenv(self) -> None:
        plain = self.root / "utilities"
        plain.mkdir()
        self.assertFalse(venv_prune.is_virtualenv(plain))

    def test_a_subdirectory_of_a_venv_is_not_itself_a_venv(self) -> None:
        """The predicate answers about ONE directory. Callers prune at the root, so
        nothing below it is ever visited -- but the answer for a nested dir must still
        be honest, or a caller that reached one another way would skip real source."""
        venv = self._make_venv(".venv")
        inner = venv / "Lib" / "site-packages"
        inner.mkdir(parents=True)
        self.assertFalse(venv_prune.is_virtualenv(inner))

    def test_empty_marker_still_counts(self) -> None:
        """PEP 405 does not require any particular key, and this module deliberately
        does not parse the file -- presence IS the contract."""
        venv = self.root / "empty-marker"
        venv.mkdir()
        (venv / _MARKER).write_text("", encoding="utf-8", newline="")
        self.assertTrue(venv_prune.is_virtualenv(venv))

    def test_marker_as_a_directory_does_not_count(self) -> None:
        """A directory named pyvenv.cfg is not the marker file, so this must stay
        False -- the check is is_file(), not exists()."""
        impostor = self.root / "impostor"
        (impostor / _MARKER).mkdir(parents=True)
        self.assertFalse(venv_prune.is_virtualenv(impostor))

    def test_a_file_path_is_not_a_virtualenv(self) -> None:
        """Callers pass directory entries, but a file must return False rather than
        raise -- a walk that tripped over one would abort the whole gate."""
        target = self.root / "module.py"
        target.write_text("x = 1\n", encoding="utf-8", newline="")
        self.assertFalse(venv_prune.is_virtualenv(target))

    def test_a_missing_path_is_not_a_virtualenv(self) -> None:
        """A dir removed between listing and checking must read False, not raise."""
        self.assertFalse(venv_prune.is_virtualenv(self.root / "gone"))


class IsUnderVirtualenvTest(unittest.TestCase):
    """The ancestor form, for gates that filter a discovered path instead of pruning."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _venv_with(self, name: str, *, relative: str) -> Path:
        """Create a venv named *name* under the root and a file at *relative* inside it."""
        venv = self.root / name
        target = venv / relative
        target.parent.mkdir(parents=True)
        (venv / _MARKER).write_text("", encoding="utf-8", newline="")
        target.write_text("", encoding="utf-8", newline="")
        return target

    def test_file_deep_inside_a_virtualenv(self) -> None:
        buried = self._venv_with(".tool-env", relative="lib/site-packages/x.py")
        self.assertTrue(venv_prune.is_under_virtualenv(buried, self.root))

    def test_file_directly_inside_a_virtualenv(self) -> None:
        shallow = self._venv_with("venv", relative="x.py")
        self.assertTrue(venv_prune.is_under_virtualenv(shallow, self.root))

    def test_file_outside_any_virtualenv(self) -> None:
        plain = self.root / "utilities" / "tool" / "tool.py"
        plain.parent.mkdir(parents=True)
        plain.write_text("", encoding="utf-8", newline="")
        self.assertFalse(venv_prune.is_under_virtualenv(plain, self.root))

    def test_a_path_outside_root_exhausts_the_walk(self) -> None:
        """When *root* is not an ancestor at all, the walk runs out of parents rather
        than looping forever, and an unrelated path is simply not in a virtualenv."""
        with tempfile.TemporaryDirectory() as other:
            stray = Path(other) / "x.py"
            stray.write_text("", encoding="utf-8", newline="")
            self.assertFalse(venv_prune.is_under_virtualenv(stray, self.root))

    def test_the_root_itself_is_never_tested(self) -> None:
        """The caller chose to scan *root*; a marker there must not blank the whole run.
        Stopping at root also bounds the walk, so it cannot escape upward."""
        (self.root / _MARKER).write_text("", encoding="utf-8", newline="")
        target = self.root / "x.py"
        target.write_text("", encoding="utf-8", newline="")
        self.assertFalse(venv_prune.is_under_virtualenv(target, self.root))


if __name__ == "__main__":
    unittest.main()
