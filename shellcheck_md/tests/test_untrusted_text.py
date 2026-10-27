"""Grouped suite: text the tool did not write is escaped before it reaches a terminal.

A Markdown file's NAME, a finding's message and ShellCheck's own output all come from
outside the tool. Printed raw, an escape sequence in any of them drives the terminal or CI
log it lands in, and a direction override makes one name display as another. Every human
facing line therefore goes through ``stdio_utf8.spell_unsafe``, which spells each such
character as a visible escape.
"""

from __future__ import annotations

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

_HOSTILE = "\x1b[31m‮"
_SPELLED = "\\x1b[31m\\u202e"
_BLOCK = "```bash\necho hi\n```\n"


def _run(argv: list[str], seam: object = None) -> tuple[int, str, str]:
    """Run ``main`` with *seam* standing in for shellcheck; return (exit, out, err).

    A case that never reaches the backend leaves *seam* as None, so a call would fail.
    """
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(sut, "_run_shellcheck", seam),
        mock.patch("sys.stdout", out),
        mock.patch("sys.stderr", err),
    ):
        code = sut.main(argv)
    return code, out.getvalue(), err.getvalue()


def _assert_no_raw_controls(case: unittest.TestCase, text: str) -> None:
    """Neither the escape character nor the override may survive into *text*."""
    case.assertNotIn("\x1b", text)
    case.assertNotIn("‮", text)


class ReportTests(unittest.TestCase):
    def test_a_findings_name_and_message_are_spelled_out(self) -> None:
        finding = sut.Finding(
            source=Path(f"doc{_HOSTILE}.md"),
            line=2,
            column=1,
            severity="warning",
            code="SC2086",
            message=f"Quote{_HOSTILE} it.",
        )
        out = io.StringIO()
        sut._report([finding], 1, out)
        text = out.getvalue()
        _assert_no_raw_controls(self, text)
        self.assertIn(
            f"doc{_SPELLED}.md:2:1: warning: Quote{_SPELLED} it. [SC2086]", text
        )

    @unittest.skipIf(os.name == "nt", "Windows refuses a control character in a name")
    def test_a_real_files_hostile_name_is_spelled_in_its_finding(self) -> None:
        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 1, f"{paths[0]}:2:6: note: Quote it. [SC2086]\n"

        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / f"doc{_HOSTILE}.md"
            md.write_text(_BLOCK, encoding="utf-8", newline="\n")
            code, out, _err = _run(["--files", str(md)], fake)
        self.assertEqual(code, 1)
        _assert_no_raw_controls(self, out)
        self.assertIn(f"doc{_SPELLED}.md:2:6: note:", out)


class ErrorPathTests(unittest.TestCase):
    def test_a_missing_files_name_is_spelled_out(self) -> None:
        code, _out, err = _run(["--files", f"gone{_HOSTILE}.md"])
        self.assertEqual(code, 2)
        _assert_no_raw_controls(self, err)
        self.assertIn(f"not a file: gone{_SPELLED}.md", err)

    def test_shellchecks_output_tail_is_spelled_out(self) -> None:
        def fake(_exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 3, f"boom{_HOSTILE}\nsecond line\n"

        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "doc.md"
            md.write_text(_BLOCK, encoding="utf-8", newline="\n")
            code, _out, err = _run(["--files", str(md)], fake)
        self.assertEqual(code, 2)
        _assert_no_raw_controls(self, err)
        self.assertIn(f"boom{_SPELLED}", err)
        # A line separator inside the output is one line of it, not a forged second line.
        self.assertIn("second\\u2028line", err)

    def test_an_unreadable_files_name_is_spelled_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            md = Path(tmp) / "bad‮.md"
            md.write_bytes(b"\xff\xfe not utf-8")
            code, _out, err = _run(["--files", str(md)])
        self.assertEqual(code, 2)
        _assert_no_raw_controls(self, err)
        self.assertIn("bad\\u202e.md", err)


class ArgumentParserMessageTests(unittest.TestCase):
    """argparse's own usage and error text quotes argv, so it is escaped too."""

    def test_an_unrecognized_arguments_escape_is_spelled_out(self) -> None:
        err = io.StringIO()
        with (
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", err),
            self.assertRaises(SystemExit) as caught,
        ):
            sut.main(["x\x1b[31m.md"])
        self.assertEqual(caught.exception.code, 2)
        text = err.getvalue()
        self.assertNotIn("\x1b", text)
        self.assertIn("unrecognized arguments: x\\x1b[31m.md", text)
        # The usage line and the error stay two real lines, not one escaped run.
        self.assertNotIn("\\x0a", text)
        self.assertGreaterEqual(text.count("\n"), 2)

    def test_the_parser_is_built_on_the_escaping_parser(self) -> None:
        self.assertIsInstance(sut.build_parser(), sut.stdio_utf8.EscapingArgumentParser)


if __name__ == "__main__":
    unittest.main()
