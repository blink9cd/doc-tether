"""Grouped suite: inputs a real tree throws at the gate that a tidy fixture never does.

Each test here pins one way the gate used to misread or crash on an ordinary tree: a help
text naming another option, a source file or doc that is not UTF-8, a build directory left
by ``pip install .``, an option with several long spellings, a ``--[no-]flag`` row, a
``#`` comment inside a fenced block, a run that examined nothing, and a live ``--help``
render that failed. They are grouped by that shared question (does the gate survive the
tree as it really is?) rather than spread across the suites of the modules they touch.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir

_CLI_SOURCE = "import argparse\nargparse.ArgumentParser()\n"


class _ScratchMixin(unittest.TestCase):
    """A per-test scratch directory, removed when the test ends."""

    def _scratch(self) -> Path:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        return Path(scratch.name)

    def _run(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(args)
        return code, out.getvalue(), err.getvalue()


class OptionColumnTest(unittest.TestCase):
    """A default belongs to the option in the entry's option column, not one its help names."""

    _HELP = textwrap.dedent(
        """\
        options:
          --retries N  how many (default: 3)
          -r R         retry; see --retries (default: 8)
          -w W
                       wrapped; see --retries (default: 9)
        """
    )

    def test_live_render_defaults_ignore_names_in_help_text(self) -> None:
        self.assertEqual(mod.parse_help_defaults(self._HELP), {"--retries": "3"})

    def test_help_block_defaults_ignore_names_in_help_text(self) -> None:
        md = "```\nusage: t\n\n" + self._HELP + "```\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(block.options, {"--retries": "3"})


class EncodingTest(_ScratchMixin):
    """Source is decoded as Python decodes it; an undecodable file never crashes the gate."""

    def test_a_coding_cookie_is_honored(self) -> None:
        root = self._scratch()
        source = "# -*- coding: latin-1 -*-\n# caf\xe9\n" + _CLI_SOURCE
        (root / "tool.py").write_bytes(source.encode("latin-1"))
        found = mod.discover_cli_files(root, (), read_text=mod._read_source)
        self.assertEqual(found, [root / "tool.py"])

    def test_an_undecodable_file_in_a_walk_is_skipped(self) -> None:
        root = self._scratch()
        (root / "bad.py").write_bytes(b"# caf\xe9\n" + _CLI_SOURCE.encode())
        (root / "late.py").write_bytes(_CLI_SOURCE.encode() + b"# caf\xe9\n")
        (root / "good.py").write_bytes(_CLI_SOURCE.encode())
        found = mod.discover_cli_files(root, (), read_text=mod._read_source)
        self.assertEqual(found, [root / "good.py"])

    def test_an_undecodable_named_path_is_a_usage_error(self) -> None:
        # A bad byte past line 2 is a decode error; in lines 1-2 Python reads it as a
        # missing encoding declaration. Both stop the run with exit 2, never a traceback.
        root = self._scratch()
        (root / "late.py").write_bytes(_CLI_SOURCE.encode() + b"# caf\xe9\n")
        (root / "early.py").write_bytes(b"# caf\xe9\n" + _CLI_SOURCE.encode())
        code, _out, err = self._run(["--path", str(root / "late.py")])
        self.assertEqual(code, 2)
        self.assertIn("cannot decode", err)
        code, _out, err = self._run(["--path", str(root / "early.py")])
        self.assertEqual(code, 2)
        self.assertIn("encoding declaration", err)

    def test_an_undecodable_doc_is_a_usage_error(self) -> None:
        root = self._scratch()
        (root / "tool.py").write_bytes(_CLI_SOURCE.encode())
        (root / "tool.md").write_bytes(b"| Option |\n| --- |\n| caf\xe9 |\n")
        code, _out, err = self._run(["--path", str(root / "tool.py")])
        self.assertEqual(code, 2)
        self.assertIn("cannot decode", err)


class BuildDirectoryTest(_ScratchMixin):
    def test_packaging_output_directories_are_pruned(self) -> None:
        root = self._scratch()
        for name in ("build", "dist", ".tox", ".nox"):
            (root / name / "lib").mkdir(parents=True)
            (root / name / "lib" / "tool.py").write_bytes(_CLI_SOURCE.encode())
        (root / "tool.py").write_bytes(_CLI_SOURCE.encode())
        found = mod.discover_cli_files(root, (), read_text=mod._read_source)
        self.assertEqual(found, [root / "tool.py"])


class AliasTest(unittest.TestCase):
    """Every long spelling of an option names it, on both sides of the comparison."""

    _TREE = """
        import argparse
        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--dry-run", "--dryrun", help="c")
            p.add_argument("--fancy", action=argparse.BooleanOptionalAction, help="f")
            return 0
        """

    def _findings(self, md: str) -> list[mod.Finding]:
        return mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            parse_module(self._TREE),
            md,
            render_help=lambda _p, _py: None,
            python="py",
        )

    def test_the_ast_reports_every_long_spelling(self) -> None:
        options = {
            o.name: o.aliases
            for o in mod.cli_options_from_ast(parse_module(self._TREE))
        }
        self.assertEqual(options["--dry-run"], ("--dry-run", "--dryrun"))
        self.assertEqual(options["--fancy"], ("--fancy", "--no-fancy"))

    def test_a_row_listing_every_alias_is_clean(self) -> None:
        md = "| Option | |\n| --- | --- |\n| `--dry-run`, `--dryrun` | c |\n| `--[no-]fancy` | f |\n"
        self.assertEqual(self._findings(md), [])

    def test_any_one_alias_covers_the_option(self) -> None:
        md = "| Option | |\n| --- | --- |\n| `--dryrun` | c |\n| `--no-fancy` | f |\n"
        self.assertEqual(self._findings(md), [])

    def test_a_negatable_row_yields_both_names(self) -> None:
        (table,) = mod.parse_md_arg_tables("| Flag |\n| --- |\n| `--[no-]fancy` |\n")
        self.assertEqual(set(table.options), {"--fancy", "--no-fancy"})


class FencedHeadingTest(unittest.TestCase):
    def test_a_comment_in_a_fence_does_not_end_the_section(self) -> None:
        md = "## Exit codes\n\n```bash\n# run it\ntool\n```\n\n- `2` -- bad input\n\n## Next\n- `5`\n"
        self.assertEqual(mod.parse_md_exit_codes(md), {2})


class NothingExaminedTest(_ScratchMixin):
    def test_text_report_says_nothing_was_examined(self) -> None:
        text = mod.format_report({}, "cli_doc_consistency.py", as_json=False)
        self.assertIn("NOTHING EXAMINED", text)
        self.assertIn("FINAL RESULT: PASS", text)

    def test_json_report_carries_the_caveat(self) -> None:
        report = json.loads(
            mod.format_report({}, "cli_doc_consistency.py", as_json=True)
        )
        self.assertEqual(report["examined"], 0)
        self.assertIn("NOTHING EXAMINED", report["caveat"])
        hidden = json.loads(
            mod.format_report({}, "cli_doc_consistency.py", as_json=True, hidden=2)
        )
        self.assertIn("all 2 candidate", hidden["caveat"])
        examined = json.loads(
            mod.format_report(
                {Path("a.py"): []}, "cli_doc_consistency.py", as_json=True
            )
        )
        self.assertIsNone(examined["caveat"])

    def test_a_folder_with_no_cli_still_exits_zero(self) -> None:
        root = self._scratch()
        (root / "lib.py").write_bytes(b"x = 1\n")
        code, out, _err = self._run(["--path", str(root)])
        self.assertEqual(code, 0)
        self.assertIn("NOTHING EXAMINED", out)


class RenderFailureTest(unittest.TestCase):
    """A requested live render that failed is said, once, rather than hidden."""

    _MD = "| Option | Default |\n| --- | --- |\n| `--count N` | 5 |\n"
    _TREE = """
        import argparse
        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--count", default=5, help="c")
            return 0
        """

    def _findings(self, rendered: str | mod.RenderFailure | None) -> list[mod.Finding]:
        return mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            parse_module(self._TREE),
            self._MD,
            render_help=lambda _p, _py: rendered,
            python="py",
            run_help=True,
        )

    def test_a_failed_render_is_one_advisory(self) -> None:
        (finding,) = self._findings(mod.RenderFailure("exit status 1: ImportError: x"))
        self.assertEqual(finding.severity, mod.SEVERITY_ADVISORY)
        self.assertIn("exit status 1: ImportError: x", finding.message)
        self.assertIn("source", finding.message)

    def test_an_unexplained_failure_is_still_said(self) -> None:
        (finding,) = self._findings(None)
        self.assertEqual(finding.severity, mod.SEVERITY_ADVISORY)

    def test_a_good_render_says_nothing(self) -> None:
        self.assertEqual(self._findings("options:\n  --count N  c (default: 5)\n"), [])

    def test_the_reason_is_ascii_and_one_line(self) -> None:
        failure = mod._failed_render(3, "\n caf\xe9 broke \nsecond line\n")
        self.assertEqual(failure.reason, "exit status 3: caf\\xe9 broke")
        self.assertEqual(mod._failed_render(1, "").reason, "exit status 1")


if __name__ == "__main__":
    unittest.main()
