"""Grouped suite: shellcheck's output back into source-mapped findings, and the report.

The pure back half -- parsing one gcc-format line, remapping it onto its Markdown source,
and rendering the run's report (including the NOTHING EXAMINED caveat). Separated from the
front half because a failure here means the tool misread shellcheck, not that it misread
Markdown.
"""

from __future__ import annotations

import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above


class ParseShellcheckLineTests(unittest.TestCase):
    def test_parses_windows_path_line(self) -> None:
        line = r"C:\tmp\0001_x__blk1__L7.sh:4:1: warning: Use cd ... [SC2164]"
        parsed = sut.parse_shellcheck_line(line)
        assert parsed is not None
        self.assertEqual(parsed.path, r"C:\tmp\0001_x__blk1__L7.sh")
        self.assertEqual((parsed.line, parsed.column), (4, 1))
        self.assertEqual((parsed.severity, parsed.code), ("warning", "SC2164"))

    def test_non_finding_line_is_none(self) -> None:
        self.assertIsNone(sut.parse_shellcheck_line("not a finding"))


class FindingsFromOutputTests(unittest.TestCase):
    def test_maps_known_and_skips_rest(self) -> None:
        block = sut.ShellBlock(Path("notes.md"), 1, 20, "bash", ("echo",))
        output = (
            "/tmp/s.sh:2:3: warning: Thing. [SC2086]\n"  # maps -> source line 21
            "/tmp/other.sh:2:1: note: x. [SC2046]\n"  # unknown path -> skipped
            "garbage line\n"  # unparseable -> skipped
        )
        parsed = sut._findings_from_output(output, {sut._norm("/tmp/s.sh"): block})
        findings = parsed.findings
        self.assertEqual(len(findings), 1)
        # A parsed finding on a path this run never wrote is kept apart, not dropped:
        # its exit status alone cannot vouch for a run whose findings all missed.
        self.assertEqual(parsed.unmapped, ("/tmp/other.sh:2:1: note: x. [SC2046]",))
        self.assertEqual(findings[0].source, Path("notes.md"))
        self.assertEqual((findings[0].line, findings[0].code), (21, "SC2086"))


class ReportTests(unittest.TestCase):
    def test_report_with_findings(self) -> None:
        buffer = io.StringIO()
        finding = sut.Finding(Path("n.md"), 5, 2, "warning", "SC2164", "Use cd")
        sut._report([finding], 1, buffer)
        text = buffer.getvalue()
        self.assertIn("n.md:5:2: warning: Use cd [SC2164]", text)
        self.assertIn("1 Markdown file(s): 1 shellcheck finding(s)", text)

    def test_report_without_findings(self) -> None:
        buffer = io.StringIO()
        sut._report([], 3, buffer)
        self.assertIn("3 Markdown file(s): 0 shellcheck finding(s)", buffer.getvalue())


class NothingExaminedWiringTest(unittest.TestCase):
    """The gate must SAY it examined nothing when the excludes hid every candidate.

    A bare "0 checked" + PASS is indistinguishable from a verified-clean run, so an
    exclude broader than its author meant would report a clean gate forever. The wording
    and the when-to-speak rule are tested exhaustively in nothing_examined's own suite; this
    only pins that this tool WIRES them into its report -- including that it cites --root,
    the target option this gate actually takes.
    """

    def _report(self, hidden: int) -> str:
        out = io.StringIO()
        sut._report([], 0, out, hidden)
        return out.getvalue()

    def test_report_names_the_hidden_count_and_cites_root(self) -> None:
        text = self._report(3)
        self.assertIn("NOTHING EXAMINED", text)
        self.assertIn("Markdown file(s)", text)
        self.assertIn("under --root", text)

    def test_report_is_silent_when_nothing_was_hidden(self) -> None:
        self.assertNotIn("NOTHING EXAMINED", self._report(0))


class RemapColumnTests(unittest.TestCase):
    """A dedented body shifts every column left; the report must shift them back.

    Stripping a nested fence's common indentation is what stops shellcheck seeing
    spurious indentation (a heredoc terminator has to reach column zero), but it moves
    each finding's column away from the Markdown position a reader will click. The
    dedent is uniform per block, so putting it back is exact rather than approximate.
    """

    @staticmethod
    def _block(body_indent: int) -> sut.ShellBlock:
        return sut.ShellBlock(
            Path("d.md"), 1, 10, "bash", ("echo",), body_indent=body_indent
        )

    def test_body_column_is_shifted_back_by_the_dedent(self) -> None:
        self.assertEqual(sut.remap_column(self._block(2), 2, 1), 3)

    def test_flush_block_column_is_untouched(self) -> None:
        self.assertEqual(sut.remap_column(self._block(0), 2, 7), 7)

    def test_shebang_column_is_not_shifted(self) -> None:
        """Script line 1 is synthesized, so no Markdown column corresponds to it."""
        self.assertEqual(sut.remap_column(self._block(2), 1, 1), 1)


class IndentedBlockFindingTests(unittest.TestCase):
    """End of the round trip: a finding inside a list-nested fence lands on the
    real Markdown line AND the real Markdown column."""

    def test_finding_maps_to_source_line_and_column(self) -> None:
        block = sut.ShellBlock(
            Path("notes.md"), 1, 20, "bash", ("echo $x",), body_indent=2
        )
        findings = sut._findings_from_output(
            "/tmp/s.sh:2:6: note: Double quote. [SC2086]\n",
            {sut._norm("/tmp/s.sh"): block},
        ).findings
        self.assertEqual(len(findings), 1)
        self.assertEqual((findings[0].line, findings[0].column), (21, 8))
