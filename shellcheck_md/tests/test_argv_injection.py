"""Grouped suite: every token after ``--files`` is a file name, never an option.

pre-commit runs the hook's command with the staged file names appended, and the hook's
command ends in ``--files``. When argparse read those names, a staged file NAMED like an
option -- ``--shellcheck=<path>``, or an abbreviation of it -- was parsed as that option, so
a file in the repository chose which executable ran as the backend, and a payload that
printed nothing and exited 0 read as a clean pass. The tool now splits its arguments at the
first ``--files``: only what comes before is parsed, and everything after is a file name.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above

_DIRTY = "```bash\necho $x\n```\n"

# Each of these, staged as a file and appended after --files, used to be parsed as an
# option. The abbreviated one relied on argparse's allow_abbrev.
_OPTION_SHAPED_NAMES = (
    "--shellcheck=./payload.md",
    "--shell=./payload.md",
    "--lang=x.md",
    "--exclude=x.md",
    "--timeout-seconds=0.001",
    "--root",
    "--files",
    "-h",
)


class _InjectionCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.md = self.root / "a.md"
        self.md.write_text(_DIRTY, encoding="utf-8", newline="\n")
        self.seen: list[str] = []

    def _fake(
        self, exe: str, paths: Sequence[Path], _timeout: float
    ) -> tuple[int, str]:
        """Record the executable; report one finding on line 2 of every script."""
        self.seen.append(exe)
        return 1, "".join(f"{p}:2:6: note: Quote it. [SC2086]\n" for p in paths)

    def _run(self, argv: list[str]) -> tuple[int, str, str]:
        """Run ``main`` from the temp root with the fake backend; parser exits included."""
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.chdir(self.root),
            mock.patch.object(sut, "_run_shellcheck", self._fake),
            mock.patch.object(sut, "_interpreter_scripts_dir", lambda: None),
            mock.patch("sys.stdout", out),
            mock.patch("sys.stderr", err),
        ):
            try:
                code = sut.main(argv)
            except SystemExit as exc:  # the parser's own refusal
                code = int(exc.code or 0)
        return code, out.getvalue(), err.getvalue()


class OptionShapedNamesTests(_InjectionCase):
    def test_each_option_shaped_name_is_a_missing_file(self) -> None:
        for name in _OPTION_SHAPED_NAMES:
            with self.subTest(name=name):
                self.seen.clear()
                code, out, err = self._run(["--files", "a.md", name])
                self.assertEqual(code, 2)
                self.assertEqual(out, "")
                self.assertEqual(err, f"ERROR: not a file: {name}\n")
                self.assertEqual(self.seen, [], "nothing may run on a refused command")

    def test_an_option_shaped_name_that_exists_is_linted_as_a_file(self) -> None:
        (self.root / "--shellcheck=.").mkdir()
        (self.root / "--shellcheck=." / "payload.md").write_text(
            _DIRTY, encoding="utf-8", newline="\n"
        )
        code, out, _err = self._run(["--files", "a.md", "--shellcheck=./payload.md"])
        self.assertEqual(code, 1)
        self.assertEqual(self.seen, ["shellcheck"], "the default backend, not the file")
        self.assertIn(f"{Path('--shellcheck=.') / 'payload.md'}:2:6:", out)
        self.assertIn("a.md:2:6:", out)
        self.assertIn("Checked 2 Markdown file(s): 2 shellcheck finding(s).", out)

    def test_the_equals_form_starts_the_file_list_too(self) -> None:
        code, _out, err = self._run(["--files=a.md", "--lang=x.md"])
        self.assertEqual(code, 2)
        self.assertEqual(err, "ERROR: not a file: --lang=x.md\n")

    def test_options_before_files_still_apply(self) -> None:
        code, out, _err = self._run(["--lang", "ksh", "--files", "a.md"])
        self.assertEqual(code, 0)
        self.assertIn("Checked 1 Markdown file(s): 0 shellcheck finding(s).", out)


class UsageErrorTests(_InjectionCase):
    def test_files_with_nothing_after_it_is_a_usage_error(self) -> None:
        code, out, err = self._run(["--files"])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertTrue(
            err.endswith("error: argument --files: expected at least one argument\n"),
            err,
        )

    def test_an_abbreviated_option_is_not_accepted(self) -> None:
        code, _out, err = self._run(["--shell", "x", "--files", "a.md"])
        self.assertEqual(code, 2)
        self.assertTrue(err.endswith("error: unrecognized arguments: --shell x\n"), err)

    def test_root_and_files_together_are_still_refused(self) -> None:
        code, _out, err = self._run(["--root", ".", "--files", "a.md"])
        self.assertEqual(code, 2)
        self.assertTrue(err.endswith("error: give --root or --files, not both\n"), err)

    def test_help_says_files_must_come_last(self) -> None:
        out = io.StringIO()
        with mock.patch("sys.stdout", out), contextlib.suppress(SystemExit):
            sut.main(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn(
            "--files must be last: every argument after it is a file name", text
        )

    def test_the_parser_declares_the_flag_the_split_looks_for(self) -> None:
        options = sut.build_parser()._option_string_actions
        self.assertIn(sut.argv_files_split.FILES_FLAG, options)

    def test_main_with_no_argv_reads_the_process_arguments(self) -> None:
        with mock.patch.object(sys, "argv", ["shellcheck_md", "--files", "a.md"]):
            code, _out, _err = self._run_none()
        self.assertEqual(code, 1)

    def _run_none(self) -> tuple[int, str, str]:
        """As ``_run``, but calling ``main()`` with no argument."""
        out, err = io.StringIO(), io.StringIO()
        with (
            contextlib.chdir(self.root),
            mock.patch.object(sut, "_run_shellcheck", self._fake),
            mock.patch("sys.stdout", out),
            mock.patch("sys.stderr", err),
        ):
            code = sut.main()
        return code, out.getvalue(), err.getvalue()


class SplitTests(unittest.TestCase):
    def test_no_files_token_leaves_every_token_to_the_parser(self) -> None:
        self.assertEqual(sut.split_at_files(["--root", "."]), (["--root", "."], None))

    def test_the_first_files_token_splits_and_later_ones_are_names(self) -> None:
        self.assertEqual(
            sut.split_at_files(["--lang", "sh", "--files", "a", "--files", "--root"]),
            (["--lang", "sh"], ["a", "--files", "--root"]),
        )

    def test_the_equals_form_keeps_its_value_as_the_first_name(self) -> None:
        self.assertEqual(sut.split_at_files(["--files=a", "b"]), ([], ["a", "b"]))


if __name__ == "__main__":
    unittest.main()
