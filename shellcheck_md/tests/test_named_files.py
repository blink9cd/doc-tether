"""Grouped suite: Markdown files named on the command line instead of discovered.

A pre-commit hook appends the staged filenames to its command, so a hook ending in
``--files`` hands the tool exactly the files to lint rather than a root to search. These
cases pin that mode: which files it examines, how the two exclude sources still bind, and
how it refuses a command that names both inputs, neither, or a file that is not there.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above

# A bash fence the fake backend below reports one finding on, per file it is handed.
_DIRTY = "```bash\necho $x\n```\n"


def _fake_finding_per_script(
    _exe: str, paths: Sequence[Path], _timeout: float
) -> tuple[int, str]:
    """Stand in for shellcheck: one finding on line 2 of every script it is given."""
    return 1, "".join(f"{p}:2:6: note: Quote it. [SC2086]\n" for p in paths)


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run ``main`` on *argv* with the faked backend; return (exit, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(sut, "_run_shellcheck", _fake_finding_per_script),
        mock.patch("sys.stdout", out),
        mock.patch("sys.stderr", err),
    ):
        try:
            code = sut.main(argv)
        except SystemExit as exc:  # the parser's own refusal
            code = int(exc.code or 0)
    return code, out.getvalue(), err.getvalue()


class NamedFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()

    def _write(self, name: str, body: str = _DIRTY) -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="")
        return path

    def test_only_the_named_files_are_linted(self) -> None:
        named = self._write("named.md")
        self._write("other.md")
        code, out, _err = _run(["--files", str(named)])
        self.assertEqual(code, 1)
        self.assertIn("named.md:2:6:", out)
        self.assertNotIn("other.md", out)
        self.assertIn("Checked 1 Markdown file(s): 1 shellcheck finding(s).", out)

    def test_a_file_named_twice_is_linted_once(self) -> None:
        named = self._write("named.md")
        code, out, _err = _run(["--files", str(named), str(named)])
        self.assertEqual(code, 1)
        self.assertIn("Checked 1 Markdown file(s): 1 shellcheck finding(s).", out)

    def test_two_spellings_of_one_file_are_linted_once(self) -> None:
        """Deduplicated on the resolved path, reported under the spelling given first."""
        named = self._write("named.md")
        dotted = self.root / "sub" / ".." / "named.md"
        (self.root / "sub").mkdir()
        code, out, _err = _run(["--files", str(dotted), str(named)])
        self.assertEqual(code, 1)
        self.assertIn("Checked 1 Markdown file(s): 1 shellcheck finding(s).", out)
        self.assertIn(f"{dotted}:2:6:", out)

    def test_clean_named_file_is_exit_0(self) -> None:
        clean = self._write("clean.md", "# no fences\n")
        code, out, _err = _run(["--files", str(clean)])
        self.assertEqual(code, 0)
        self.assertIn("Checked 1 Markdown file(s): 0 shellcheck finding(s).", out)

    def test_exclude_flag_binds_a_named_file(self) -> None:
        named = self._write("skip/named.md")
        code, out, _err = _run(
            ["--exclude", str(self.root / "skip"), "--files", str(named)]
        )
        self.assertEqual(code, 0)
        self.assertIn("NOTHING EXAMINED: all 1 candidate Markdown file(s) under", out)
        self.assertIn("the named files", out)

    def test_config_exclude_binds_a_named_file(self) -> None:
        (self.root / "pyproject.toml").write_text(
            '[tool.shellcheck_md]\nexclude = ["vendor"]\n',
            encoding="utf-8",
            newline="",
        )
        named = self._write("vendor/named.md")
        code, _out, _err = _run(["--files", str(named)])
        self.assertEqual(code, 0)

    def test_malformed_config_above_a_named_file_is_exit_2(self) -> None:
        (self.root / "pyproject.toml").write_text(
            "[tool.shellcheck_md\n", encoding="utf-8", newline=""
        )
        named = self._write("named.md")
        code, _out, err = _run(["--files", str(named)])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", err)

    def test_a_missing_named_file_is_exit_2_not_a_backend_error(self) -> None:
        # Read as it was, a missing file raised FileNotFoundError out of the lint step,
        # and main reported it as a shellcheck that could not be found.
        code, _out, err = _run(["--files", str(self.root / "absent.md")])
        self.assertEqual(code, 2)
        self.assertIn("not a file", err)
        self.assertNotIn("could not run shellcheck", err)

    def test_root_and_named_files_together_are_refused(self) -> None:
        named = self._write("named.md")
        code, _out, err = _run(["--root", str(self.root), "--files", str(named)])
        self.assertEqual(code, 2)
        self.assertIn("not both", err)

    def test_neither_root_nor_named_files_is_refused(self) -> None:
        code, _out, err = _run([])
        self.assertEqual(code, 2)
        self.assertIn("--root", err)
        self.assertNotIn("not both", err)


class SelectNamedFilesTest(unittest.TestCase):
    def test_keeps_order_free_unique_and_drops_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            keep_b, keep_a = root / "b.md", root / "a.md"
            gone = root / "gone" / "c.md"
            gone.parent.mkdir()
            for path in (keep_b, keep_a, gone):
                path.write_text("x\n", encoding="utf-8", newline="")
            selected = sut.select_named_files(
                [keep_b, keep_a, keep_b, gone], [gone.parent], stop=root
            )
        self.assertEqual(selected, [keep_a, keep_b])
