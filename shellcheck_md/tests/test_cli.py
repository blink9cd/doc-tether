"""Grouped suite: the CLI surface -- exit codes, options, and the --help contract.

main()'s three exit codes and the parser's binding to the shared help formatter. A failure
here is about what the process reports, not about what it computed.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - position only; keep below the subject import


class MainTests(unittest.TestCase):
    @staticmethod
    def _write_md(directory: str, body: str) -> None:
        (Path(directory) / "x.md").write_text(body, encoding="utf-8", newline="")

    def test_main_returns_one_on_findings(self) -> None:
        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 1, f"{paths[0]}:2:1: warning: Issue. [SC1234]\n"

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory, "```bash\nbad\n```\n")
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                self.assertEqual(sut.main(["--root", directory]), 1)

    def test_main_returns_zero_when_clean(self) -> None:
        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory, "```bash\necho ok\n```\n")
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                self.assertEqual(sut.main(["--root", directory, "--lang", "bash"]), 0)

    def test_main_honors_exclude(self) -> None:
        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 1, "".join(f"{p}:2:1: warning: Issue. [SC1234]\n" for p in paths)

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory, "```bash\nbad\n```\n")  # writes x.md
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                code = sut.main(
                    ["--root", directory, "--exclude", str(Path(directory) / "x.md")]
                )
            # The only Markdown file is excluded -> no blocks -> clean exit.
            self.assertEqual(code, 0)

    def test_main_absent_shellcheck_is_exit_2(self) -> None:
        # A missing shellcheck backend + a bash fence -> exit 2 ("could not run"), never a
        # traceback (regression: main used to let FileNotFoundError escape). It
        # matters on a fresh box (e.g. macOS before `brew install shellcheck`).
        def fake(_exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            raise FileNotFoundError("shellcheck")

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory, "```bash\necho hi\n```\n")
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch("sys.stdout", io.StringIO()),
                mock.patch("sys.stderr", io.StringIO()) as err,
            ):
                code = sut.main(["--root", directory])
        self.assertEqual(code, 2)
        self.assertIn("could not run shellcheck", err.getvalue())


class FormatterWiringTest(unittest.TestCase):
    """Assert the parser is WIRED to the shared formatter, not that it re-implements it.

    Why this is not a rendering assertion: ``help_formatter.md``, "The version trap".
    """

    def test_parser_uses_the_shared_formatter(self) -> None:
        formatter_class = sut.build_parser().formatter_class
        self.assertIs(formatter_class, help_formatter.UnsetHidingFormatter)


def _help_text() -> str:
    """The rendered --help, whitespace collapsed so wrapping cannot split a phrase."""
    with mock.patch("sys.stdout", io.StringIO()) as out:
        with contextlib.suppress(SystemExit):
            sut.main(["--help"])
    return " ".join(out.getvalue().split())


class HelpTextTests(unittest.TestCase):
    def test_no_option_advertises_an_unset_default(self) -> None:
        text = _help_text()
        self.assertNotIn("(default: None)", text)
        self.assertNotIn("(default: [])", text)

    def test_lang_help_says_what_omitting_it_means(self) -> None:
        text = _help_text()
        self.assertIn("omitting it keeps that set", text)
        self.assertNotIn("None leaves", text)


def _parser_error(argv: list[str]) -> str:
    """The last stderr line argparse prints when it refuses *argv*."""
    with mock.patch("sys.stderr", io.StringIO()) as err:
        with contextlib.suppress(SystemExit):
            sut.main(argv)
    return err.getvalue().strip().splitlines()[-1]


class UsageErrorTests(unittest.TestCase):
    """Neither input and both inputs are different mistakes, so they read differently."""

    def test_neither_input_asks_for_one(self) -> None:
        self.assertTrue(_parser_error([]).endswith("error: give --root or --files"))

    def test_both_inputs_says_not_both(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "x.md"
            md.write_text("# x\n", encoding="utf-8", newline="\n")
            line = _parser_error(["--root", directory, "--files", str(md)])
        self.assertTrue(line.endswith("error: give --root or --files, not both"))


class DefaultShellcheckTests(unittest.TestCase):
    """With no --shellcheck, the copy installed beside this interpreter comes first.

    A pipx install of the package puts shellcheck-py's binary in the environment's own
    scripts directory, which is not on PATH; only the tool's own entry point is linked
    out. Looking there first finds the binary the package brought with it.
    """

    def _seen_executable(self, scripts_dir: str | None, argv: list[str]) -> str:
        seen: list[str] = []

        def fake(exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            seen.append(exe)
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "x.md").write_text(
                "```bash\necho hi\n```\n", encoding="utf-8", newline="\n"
            )
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch.object(sut, "_interpreter_scripts_dir", lambda: scripts_dir),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                sut.main(["--root", directory, *argv])
        return seen[0]

    def test_the_interpreters_own_copy_is_preferred(self) -> None:
        with tempfile.TemporaryDirectory() as scripts:
            name = "shellcheck.exe" if os.name == "nt" else "shellcheck"
            installed = Path(scripts) / name
            installed.write_text("", encoding="utf-8", newline="\n")
            self.assertEqual(self._seen_executable(scripts, []), str(installed))

    def test_without_one_the_name_on_path_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as scripts:
            self.assertEqual(self._seen_executable(scripts, []), "shellcheck")

    def test_an_explicit_option_wins(self) -> None:
        with tempfile.TemporaryDirectory() as scripts:
            (Path(scripts) / "shellcheck").write_text(
                "", encoding="utf-8", newline="\n"
            )
            self.assertEqual(
                self._seen_executable(scripts, ["--shellcheck", "mine"]), "mine"
            )

    def test_the_resolver_names_the_windows_executable(self) -> None:
        with tempfile.TemporaryDirectory() as scripts:
            exe = Path(scripts) / "shellcheck.exe"
            exe.write_text("", encoding="utf-8", newline="\n")
            self.assertEqual(
                sut.resolve_default_shellcheck(scripts, windows=True), str(exe)
            )
            self.assertEqual(
                sut.resolve_default_shellcheck(scripts, windows=False), "shellcheck"
            )

    def test_no_scripts_directory_falls_back_to_path(self) -> None:
        self.assertEqual(
            sut.resolve_default_shellcheck(None, windows=False), "shellcheck"
        )
