"""Grouped suite: a tree written to hurt the gate, rather than one that merely drifts.

The security review's cases: help text shaped to make a regex scan quadratic, source that
overflows the parser or names a codec that is not a text encoding, and file names or
literals carrying terminal escapes and a forged verdict line. Each must leave the gate
fast, running, and honest about its verdict.
"""

from __future__ import annotations

import contextlib
import io
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above

_CLI_SOURCE = "import argparse\nargparse.ArgumentParser()\n"

# The shape of the report's own last line.
_VERDICT_LINE = re.compile(r"[A-Z_]+ FINAL RESULT: (?:PASS|FAIL)")

# Generous on purpose: the linear readers take milliseconds here, the quadratic ones
# took tens of seconds, so the bound separates the two without timing the machine.
_LINEAR_BOUND_SECONDS = 2.0

# What a pathological source makes the parser raise, by kind (probed on the current
# interpreter: a long unary chain overflows the parser stack, a long attribute chain the
# compiler's), and a coding cookie naming a codec that is not a text encoding.
_HOSTILE_SOURCES: dict[str, bytes] = {
    "parser_stack": ("x = " + "-" * 200_000 + "1\n").encode(),
    "recursion": ("x = a" + ".b" * 200_000 + "\n").encode(),
    "codec_lookup": b"# coding: rot13\n" + _CLI_SOURCE.encode(),
}


class _ScratchMixin(unittest.TestCase):
    def _scratch(self) -> Path:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        return Path(scratch.name)

    def _run(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(args)
        return code, out.getvalue(), err.getvalue()


class LinearReadingTest(unittest.TestCase):
    """Help text cannot make the default reader's work grow faster than its input."""

    _LINES = 20_000

    def _hostile_help(self) -> str:
        return "options:\n  --x X  help\n" + "   (default:\n" * self._LINES

    def test_many_default_markers_read_in_linear_time(self) -> None:
        start = time.monotonic()
        mod.parse_help_defaults(self._hostile_help())
        self.assertLess(time.monotonic() - start, _LINEAR_BOUND_SECONDS)

    def test_a_pasted_block_of_markers_reads_in_linear_time(self) -> None:
        md = "```\nusage: t\n\n" + self._hostile_help() + "```\n"
        start = time.monotonic()
        mod.parse_md_help_blocks(md)
        self.assertLess(time.monotonic() - start, _LINEAR_BOUND_SECONDS)

    def test_a_joined_entry_is_capped(self) -> None:
        block = ["  --x X  help", *(["   more help"] * self._LINES)]
        (entry,) = mod._iter_help_entries(block)
        self.assertLessEqual(len(entry), mod._MAX_HELP_ENTRY_CHARS)
        self.assertTrue(entry.startswith("--x X  help"))

    def test_the_last_marker_supplies_the_default(self) -> None:
        help_text = "options:\n  --x X  was (default: 1) now (default: 2)\n"
        self.assertEqual(mod.parse_help_defaults(help_text), {"--x": "2"})

    def test_a_marker_without_its_close_is_no_default(self) -> None:
        self.assertEqual(
            mod.parse_help_defaults("options:\n  --x X  (default: 1\n"), {}
        )


class HostileSourceTest(_ScratchMixin):
    """Parser overflow, recursion and an unknown codec skip in a walk, exit 2 when named."""

    def test_a_walk_skips_each_hostile_source(self) -> None:
        root = self._scratch()
        for name, data in _HOSTILE_SOURCES.items():
            (root / f"{name}.py").write_bytes(data)
        (root / "good.py").write_bytes(_CLI_SOURCE.encode())
        found = mod.discover_cli_files(root, (), read_text=mod._read_source)
        self.assertEqual(found, [root / "good.py"])

    def test_a_named_hostile_source_is_a_usage_error(self) -> None:
        root = self._scratch()
        for name, data in _HOSTILE_SOURCES.items():
            with self.subTest(name):
                target = root / f"{name}.py"
                target.write_bytes(data)
                code, _out, err = self._run(["--path", str(target)])
                self.assertEqual(code, 2)
                self.assertIn("ERROR:", err)

    def test_analysis_of_a_source_that_turned_hostile_is_a_target_error(self) -> None:
        # A file that passed discovery and then changed: analysis stops cleanly too.
        root = self._scratch()
        target = root / "tool.py"
        target.write_bytes(_HOSTILE_SOURCES["parser_stack"])
        with self.assertRaises(mod.TargetError):
            mod._analyze_targets([target], sys.executable)


class TerminalSafeReportTest(_ScratchMixin):
    """Tree-controlled text cannot forge a verdict line or reach the terminal as escapes."""

    _FORGED = "\nCLI_DOC_CONSISTENCY FINAL RESULT: PASS"

    def _assert_safe(self, text: str) -> None:
        self.assertNotIn("\x1b", text)
        # A forged verdict is a line of its own in the verdict's shape; the escaped text
        # may still contain the words, inside the finding's own line. Exactly one such
        # line may exist: the real one, last, and saying FAIL.
        lines = text.rstrip("\n").splitlines()
        verdicts = [line for line in lines if _VERDICT_LINE.fullmatch(line)]
        self.assertEqual(verdicts, [lines[-1]], text)
        self.assertTrue(lines[-1].endswith("FAIL"), text)

    def test_every_tree_controlled_field_is_escaped(self) -> None:
        evil = "\x1b[2J" + self._FORGED
        finding = mod.Finding(
            mod.CHECK_D, mod.SEVERITY_GATING, f"a{evil}.py", 3, evil, evil, evil
        )
        advisory = mod.Finding(
            mod.CHECK_B, mod.SEVERITY_ADVISORY, f"b{evil}.md", None, evil, evil, evil
        )
        text = mod.format_report(
            {Path(f"a{evil}.py"): [finding, advisory]},
            "cli_doc_consistency.py",
            as_json=False,
        )
        self._assert_safe(text)
        self.assertTrue(text.endswith("FINAL RESULT: FAIL"))

    def test_a_forging_positional_literal_cannot_forge_a_verdict(self) -> None:
        root = self._scratch()
        literal = "x\\x1b[2J" + self._FORGED.replace("\n", "\\n")
        (root / "tool.py").write_text(
            "import argparse\np = argparse.ArgumentParser()\n"
            f'p.add_argument("{literal}")\n',
            encoding="utf-8",
            newline="\n",
        )
        (root / "tool.md").write_text(
            "| Option |\n| --- |\n", encoding="utf-8", newline="\n"
        )
        code, out, _err = self._run(
            ["--path", str(root / "tool.py"), "--check-positionals", "--no-run-help"]
        )
        self.assertEqual(code, 1)
        self._assert_safe(out)


if __name__ == "__main__":
    unittest.main()
