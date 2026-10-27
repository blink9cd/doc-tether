"""Grouped suite: driving shellcheck -- the run seam and its wall-clock bound.

Covers lint_markdown end to end against a faked seam: blocks reaching disk as LF, findings
remapped, and the bound reaching each call rather than only the outer loop.
"""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above


class LintMarkdownTests(unittest.TestCase):
    def test_no_blocks_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "x.md"
            md.write_text("no shell here\n", encoding="utf-8", newline="")
            self.assertEqual(sut.lint_markdown([md]), [])

    def test_findings_are_remapped(self) -> None:
        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 1, f"{paths[0]}:2:1: warning: Issue. [SC1234]\n"

        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "x.md"
            md.write_text(
                "intro\n```bash\nbad code\n```\n", encoding="utf-8", newline=""
            )
            with mock.patch.object(sut, "_run_shellcheck", fake):
                findings = sut.lint_markdown([md])
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].line, 3)  # body line after the fence at line 2
        self.assertEqual(findings[0].code, "SC1234")

    def test_written_scripts_use_lf_newlines(self) -> None:
        # Guards the Windows CRLF trap: shellcheck flags \r (SC1017), so the
        # generated scripts must hit disk as LF regardless of platform.
        captured: list[bytes] = []

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            captured.extend(Path(p).read_bytes() for p in paths)
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            md = Path(directory) / "x.md"
            md.write_text(
                "```bash\necho hi\necho bye\n```\n", encoding="utf-8", newline=""
            )
            with mock.patch.object(sut, "_run_shellcheck", fake):
                sut.lint_markdown([md])
        self.assertTrue(captured)
        for content in captured:
            self.assertNotIn(b"\r", content)


class TimeoutBoundTests(unittest.TestCase):
    """The shellcheck call must be bounded, and firing must be exit 2, not clean."""

    @staticmethod
    def _write_md(directory: str, body: str = "```bash\necho hi\n```\n") -> None:
        (Path(directory) / "x.md").write_text(body, encoding="utf-8", newline="\n")

    def test_default_bound_reaches_the_seam(self) -> None:
        seen: list[float] = []

        def fake(_exe: str, _paths: Sequence[Path], timeout: float) -> tuple[int, str]:
            seen.append(timeout)
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory)
            md = Path(directory) / "x.md"
            with mock.patch.object(sut, "_run_shellcheck", fake):
                sut.lint_markdown([md])
        self.assertEqual(seen, [sut.DEFAULT_TIMEOUT_SECONDS])

    def test_explicit_bound_reaches_the_seam(self) -> None:
        seen: list[float] = []

        def fake(_exe: str, _paths: Sequence[Path], timeout: float) -> tuple[int, str]:
            seen.append(timeout)
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory)
            md = Path(directory) / "x.md"
            with mock.patch.object(sut, "_run_shellcheck", fake):
                sut.lint_markdown([md], timeout=3.25)
        self.assertEqual(seen, [3.25])

    def test_timeout_is_could_not_run_not_a_clean_pass(self) -> None:
        """Exit 0 here would claim shellcheck found nothing; it never finished."""

        def hang(_exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            raise subprocess.TimeoutExpired(cmd="shellcheck", timeout=1.0)

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory)
            with (
                mock.patch.object(sut, "_run_shellcheck", hang),
                mock.patch("sys.stdout", io.StringIO()) as out,
                mock.patch("sys.stderr", io.StringIO()) as err,
            ):
                code = sut.main(["--root", directory])
        self.assertEqual(code, 2)  # same channel as a missing executable
        self.assertIn("could-not-run", err.getvalue())
        self.assertEqual(out.getvalue(), "")  # no report implying a verdict

    def test_cli_bound_reaches_the_seam(self) -> None:
        seen: list[float] = []

        def fake(_exe: str, _paths: Sequence[Path], timeout: float) -> tuple[int, str]:
            seen.append(timeout)
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            self._write_md(directory)
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch("sys.stdout", io.StringIO()),
            ):
                code = sut.main(["--root", directory, "--timeout-seconds", "19.0"])
        self.assertEqual(code, 0)
        self.assertEqual(seen, [19.0])

    def test_timeout_option_is_documented_in_help(self) -> None:
        # Driven through the CLI rather than build_parser() deliberately: what is pinned
        # here is the RENDERED text a reader sees, which only argparse's own --help path
        # produces. The parser-to-formatter binding is asserted separately, in test_cli.
        with mock.patch("sys.stdout", io.StringIO()) as out:
            with self.assertRaises(SystemExit):
                sut.main(["--help"])
        text = " ".join(out.getvalue().split())
        self.assertIn("--timeout-seconds", text)
        # Rendered from the live default= by the formatter, never hardcoded in help=.
        self.assertIn(f"default: {sut.DEFAULT_TIMEOUT_SECONDS}", text)
