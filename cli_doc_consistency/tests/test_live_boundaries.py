"""Grouped suite: the two checks that touch reality instead of a fake.

Every other module injects the --help render seam; this one runs it for real, end to end at
the subprocess boundary. Beside it, the .md's hand-transcribed code
lists checked against the shipped file. Its own module because these are the slow tests and
the only ones that can fail for reasons outside the code under test: a reader arriving here
is asking "did reality change?", not "is the logic right?". `_doc_text` lives here because
this is its only caller.
"""

from __future__ import annotations

import contextlib
import io
import os
import re
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above


class LiveRenderSmokeTest(unittest.TestCase):
    """End-to-end at the subprocess boundary: a real ``--help`` render."""

    _PY = textwrap.dedent(
        """\
        import argparse
        def main():
            p = argparse.ArgumentParser(
                formatter_class=argparse.ArgumentDefaultsHelpFormatter
            )
            p.add_argument("--path", required=True, help="p")
            p.add_argument("--count", type=int, default=7, help="how many")
            return 0
        if __name__ == "__main__":
            raise SystemExit(main())
        """
    )
    _MD_MATCH = (
        "| Option | Default | Meaning |\n| --- | --- | --- |\n"
        "| `--path P` | — | p |\n| `--count N` | 7 | how many |\n"
    )
    _MD_MISMATCH = _MD_MATCH.replace("| 7 |", "| 9 |")

    def _run_main_dir(self, md: str) -> tuple[int, str]:
        root = Path(tempfile.mkdtemp())
        (root / "pyproject.toml").write_text(
            "[tool.other]\n", encoding="utf-8", newline="\n"
        )
        (root / "tool.py").write_text(self._PY, encoding="utf-8", newline="\n")
        (root / "tool.md").write_text(md, encoding="utf-8", newline="\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.main(["--path", str(root / "tool.py"), "--run-help"])
        return code, out.getvalue()

    def test_live_render_matching_default_passes(self) -> None:
        # Real subprocess renders "(default: 7)"; the .md agrees -> clean.
        code, out = self._run_main_dir(self._MD_MATCH)
        self.assertEqual(code, 0, out)
        self.assertIn("FINAL RESULT: PASS", out)

    def test_live_render_mismatched_default_gates(self) -> None:
        # Real render says 7, the .md says 9 -> a real numeric-default catch.
        code, out = self._run_main_dir(self._MD_MISMATCH)
        self.assertEqual(code, 1, out)
        self.assertIn("--count", out)

    def test_live_render_failure_is_reported(self) -> None:
        # A tool whose --help exits 1 is really run; the advisory carries its status.
        failing = self._PY.replace(
            "def main():", "import sys\ndef main():\n    sys.exit(1)"
        )
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        (root / "tool.py").write_text(failing, encoding="utf-8", newline="\n")
        (root / "tool.md").write_text(self._MD_MATCH, encoding="utf-8", newline="\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = mod.main(["--path", str(root / "tool.py"), "--run-help"])
        self.assertEqual(code, 0, out.getvalue())
        self.assertIn("exit status 1", out.getvalue())

    def test_render_help_from_a_relative_path(self) -> None:
        # A relative --path (the usual way a sweep is run) must still render: the child
        # runs with the tool's folder as its cwd, so the path it is given must not be
        # relative to the caller's cwd. Before the fix every such render failed, exit 2.
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        (root / "pkg").mkdir()
        source = "import argparse\nargparse.ArgumentParser().parse_args()\n"
        (root / "pkg" / "tool.py").write_text(source, encoding="utf-8", newline="\n")
        previous = Path.cwd()
        self.addCleanup(os.chdir, previous)
        os.chdir(root)
        rendered = mod._render_help(Path("pkg") / "tool.py", sys.executable)
        self.assertIsInstance(rendered, str, rendered)
        self.assertIn("usage:", str(rendered))

    def test_render_help_bad_interpreter_is_a_failure(self) -> None:
        # The real seam degrades to a RenderFailure (fall back to AST) when it cannot run.
        rendered = mod._render_help(Path("x.py"), "definitely-not-a-real-binary-xyz")
        self.assertIsInstance(rendered, mod.RenderFailure)


class DocTranscriptionConsistencyTest(unittest.TestCase):
    """Lock the .md's hand-transcribed code lists against drift."""

    def test_version_gated_stdlib_documented(self) -> None:
        doc = _doc_text()
        for module, (major, minor) in mod.VERSION_GATED_STDLIB.items():
            self.assertIn(module, doc)
            self.assertIn(f"{major}.{minor}", doc)

    def test_roster_letters_match_check_constants(self) -> None:
        doc = _doc_text()
        letters = set(re.findall(r"^\| ([A-Z]): ", doc, re.MULTILINE))
        self.assertEqual(letters, {mod.CHECK_A, mod.CHECK_B, mod.CHECK_C, mod.CHECK_D})


def _doc_text() -> str:
    """The companion cli_doc_consistency.md text (resolved next to the tool)."""
    assert mod.__file__ is not None
    doc = Path(mod.__file__).resolve().parent / "cli_doc_consistency.md"
    return doc.read_text(encoding="utf-8")
