"""Grouped suite: which files get checked at all.

The standing [tool.cli_doc_consistency] exclude read, the directory walk that prunes a
virtualenv by its marker rather than its name, and the wiring that makes the gate SAY it
examined nothing. One surface, and the one where a wrong answer is SILENT: a file never
examined reports no finding, so an exclude broader than its author meant would report a
clean gate forever.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from collections.abc import Callable
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above

# cli_doc_consistency's import runs its sys.path bootstrap, so this resolves after it.
import pyproject_config  # noqa: E402 - same bootstrap


# The walk to the nearest pyproject, the parse, the list-of-strings validation and the
# resolve-against-the-file's-own-directory rule belong to pyproject_config and are pinned
# by ITS suite, so this class does not repeat them: a non-list value, a non-string member,
# a key-less table and a non-dict `tool`. No `stop=` ceiling bounds the climb either:
# faked seams mean these tests never touch a real pyproject.
class ConfigExcludesTest(unittest.TestCase):
    """[tool.cli_doc_consistency] standing excludes: which key, which container, fail-loud."""

    _BASE = Path("/proj").resolve()

    def _seams(
        self, body: str
    ) -> tuple[pyproject_config.IsFileFn, pyproject_config.ReadTextFn]:
        """Seams presenting a single pyproject at ``/proj`` holding *body*."""
        return (lambda p: p == self._BASE / "pyproject.toml"), (lambda _p: body)

    def test_no_pyproject_returns_empty(self) -> None:
        self.assertEqual(
            mod.read_config_excludes(
                self._BASE, is_file=lambda _p: False, read_text=lambda _p: ""
            ),
            (),
        )

    def test_the_exclude_key_resolves_in_configured_order(self) -> None:
        """exclude is an ordered tuple, not a set: the order given is preserved."""
        is_file, read_text = self._seams(
            '[tool.cli_doc_consistency]\nexclude = ["vendor", "scratch"]\n'
        )
        self.assertEqual(
            mod.read_config_excludes(
                self._BASE / "sub", is_file=is_file, read_text=read_text
            ),
            ((self._BASE / "vendor").resolve(), (self._BASE / "scratch").resolve()),
        )

    def test_another_tools_table_is_not_read_as_ours(self) -> None:
        is_file, read_text = self._seams('[tool.other]\nexclude = ["vendor"]\n')
        self.assertEqual(
            mod.read_config_excludes(self._BASE, is_file=is_file, read_text=read_text),
            (),
        )

    def test_malformed_toml_raises_the_shared_error(self) -> None:
        """The exit-2 class is pyproject_config's, so main must catch THAT one."""
        is_file, read_text = self._seams("[tool.cli_doc_consistency\n")
        with self.assertRaises(pyproject_config.ConfigError):
            mod.read_config_excludes(self._BASE, is_file=is_file, read_text=read_text)


class DiscoverTest(unittest.TestCase):
    def _reader(self) -> Callable[[Path], str]:
        return lambda p: p.read_text(encoding="utf-8")

    def test_named_cli_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "tool.py"
            f.write_text(
                "import argparse\nargparse.ArgumentParser()\n",
                encoding="utf-8",
                newline="\n",
            )
            self.assertEqual(
                mod.discover_cli_files(f, [], read_text=self._reader()), [f]
            )

    def test_named_non_cli_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "lib.py"
            f.write_text("x = 1\n", encoding="utf-8", newline="\n")
            self.assertEqual(
                mod.discover_cli_files(f, [], read_text=self._reader()), []
            )

    def test_named_file_oserror_is_target_error(self) -> None:
        def boom(_p: Path) -> str:
            raise OSError("nope")

        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "exists.py"
            f.write_text(
                "x = 1\n", encoding="utf-8", newline="\n"
            )  # exists, but reader raises
            with self.assertRaises(mod.TargetError):
                mod.discover_cli_files(f, [], read_text=boom)

    def test_named_file_syntax_error_is_target_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "bad.py"
            f.write_text("def (:\n", encoding="utf-8", newline="\n")
            with self.assertRaises(mod.TargetError):
                mod.discover_cli_files(f, [], read_text=self._reader())

    def test_prunes_a_virtualenv_by_its_marker_not_its_name(self) -> None:
        """Named unlike ".venv" so _DEFAULT_EXCLUDE_DIRS cannot make this pass on its
        own: a third-party CLI module has no companion doc of ours to be consistent
        with."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            venv = root / ".tool-env"
            venv.mkdir()
            (venv / "pyvenv.cfg").write_text("", encoding="utf-8", newline="")
            (venv / "vendored.py").write_text(
                "import argparse\nargparse.ArgumentParser()\n",
                encoding="utf-8",
                newline="",
            )
            found = mod.discover_cli_files(root, [], read_text=self._reader())
            self.assertEqual(found, [])

    def test_dir_walk_prunes_and_filters(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cli = "import argparse\nargparse.ArgumentParser()\n"
            (root / "tool.py").write_text(cli, encoding="utf-8", newline="\n")
            (root / "test_tool.py").write_text(
                cli, encoding="utf-8", newline="\n"
            )  # skipped (test_)
            (root / "lib.py").write_text(
                "x=1\n", encoding="utf-8", newline="\n"
            )  # skipped (non-CLI)
            (root / "broken.py").write_text(
                "def (:\n", encoding="utf-8", newline="\n"
            )  # skipped (syntax)
            (root / "__pycache__").mkdir()
            (root / "__pycache__" / "c.py").write_text(
                cli, encoding="utf-8", newline="\n"
            )  # pruned
            (root / "skip").mkdir()
            (root / "skip" / "s.py").write_text(
                cli, encoding="utf-8", newline="\n"
            )  # excluded dir
            (root / "excluded_file.py").write_text(
                cli, encoding="utf-8", newline="\n"
            )  # excluded file
            found = mod.discover_cli_files(
                root,
                [(root / "skip").resolve(), (root / "excluded_file.py").resolve()],
                read_text=self._reader(),
            )
            self.assertEqual([p.name for p in found], ["tool.py"])


class NothingExaminedWiringTest(unittest.TestCase):
    """The gate must SAY it examined nothing when the excludes hid every candidate.

    A bare "0 checked" + PASS is indistinguishable from a verified-clean run, so an
    exclude broader than its author meant would report a clean gate forever. The wording
    and the when-to-speak rule are tested exhaustively in nothing_examined's own suite; this
    only pins that this tool WIRES them into its report.
    """

    def test_report_names_the_hidden_count(self) -> None:
        text = mod.format_report({}, "prog", as_json=False, hidden=3)
        self.assertIn("NOTHING EXAMINED", text)
        self.assertIn("CLI file(s)", text)

    def test_report_is_silent_when_a_cli_was_examined(self) -> None:
        text = mod.format_report({Path("a.py"): []}, "prog", as_json=False, hidden=0)
        self.assertNotIn("NOTHING EXAMINED", text)

    def test_nothing_found_and_nothing_hidden_still_says_so(self) -> None:
        # Not the exclude wording (nothing was excluded): the tree held no CLI at all.
        text = mod.format_report({}, "prog", as_json=False, hidden=0)
        self.assertIn("no argparse CLI file", text)
        self.assertNotIn("exclude set", text)
