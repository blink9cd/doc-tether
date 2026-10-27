"""Grouped suite: a pasted ``--help`` block as the doc's option surface, and the run-help opt-in.

A README rarely carries an arguments table; it carries the tool's ``--help`` output in a
fenced block. This suite pins the reader that turns such a block into the same structure a
table becomes, and Check B's use of it: option coverage from the AST whether or not the
target may be executed, and the defaults comparison only behind ``run_help``, with one
advisory saying so when it was skipped.
"""

from __future__ import annotations

import ast
import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir

# A pasted --help block as a README carries it: aliases, a wrapped help line, an option
# whose help text mentions ANOTHER option, and a rendered default.
_HELP_BLOCK = textwrap.dedent(
    """\
    # widget

    ```text
    usage: widget.py [-h] -p PATH [--count COUNT] [--json]

    options:
      -h, --help            show this help message and exit
      -p PATH, --path PATH  the input; see also --json
      --count COUNT, --number COUNT
                            how many (default: 5)
      --json                emit JSON
    ```
    """
)


class ParseHelpBlocksTest(unittest.TestCase):
    """The fenced ``usage:`` block reader in ``clidoc_docread``."""

    def test_names_come_from_the_option_column_only(self) -> None:
        (block,) = mod.parse_md_help_blocks(_HELP_BLOCK)
        # One name per entry, the first LONG string -- what the AST calls the option --
        # so -p and the --number alias are not names; --json named inside --path's HELP
        # TEXT is not counted either.
        self.assertEqual(set(block.options), {"--help", "--path", "--count", "--json"})

    def test_rendered_default_is_read(self) -> None:
        (block,) = mod.parse_md_help_blocks(_HELP_BLOCK)
        self.assertEqual(block.options["--count"], "5")
        self.assertIsNone(block.options["--json"])
        self.assertTrue(block.has_default_column)

    def test_block_without_defaults_has_no_default_column(self) -> None:
        md = "~~~\nusage: t [--json]\n\noptions:\n  --json  emit\n~~~\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(block.options, {"--json": None})
        self.assertFalse(block.has_default_column)

    def test_tilde_fence_and_case_insensitive_usage(self) -> None:
        md = "~~~~ console\n\nUSAGE: t [--x X]\n\noptions:\n  --x X  x\n~~~~\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_a_fence_not_opening_with_usage_is_ignored(self) -> None:
        md = "```bash\npython t.py --path P\n\noptions:\n  --path P  p\n```\n"
        self.assertEqual(mod.parse_md_help_blocks(md), [])

    def test_an_empty_fence_is_ignored(self) -> None:
        self.assertEqual(mod.parse_md_help_blocks("```\n\n```\n"), [])

    def test_indented_fence_content_is_dedented(self) -> None:
        # A block inside a list item: the fence's own indent is removed from its body.
        md = "- run it:\n\n  ```text\n  usage: t [--x X]\n\n  options:\n    --x X  x\n  ```\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_a_shorter_or_other_fence_does_not_close(self) -> None:
        # The inner fences sit before the options section, which a column-0 line closes.
        md = "````\nusage: t\n```\n~~~\n\noptions:\n  --x X  x\n````\n  --y Y  y\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_an_inner_fence_inside_the_options_section_does_not_close(self) -> None:
        # Shorter and other-character fence lines INSIDE the options section. Indented, as
        # section content is, they close neither the fence nor the section, so --z after
        # them is read; --y after the real closer is not.
        md = (
            "````\nusage: t\n\noptions:\n  --x X  x\n  ```\n  ~~~\n  --z Z  z\n"
            "````\n  --y Y  y\n"
        )
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x", "--z"})

    def test_an_unclosed_fence_runs_to_the_end(self) -> None:
        md = "```\nusage: t\n\noptions:\n  --x X  x\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_a_short_only_entry_has_no_name(self) -> None:
        md = "```\nusage: t [-v]\n\noptions:\n  -v  verbose\n  --x X  x\n```\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_two_blocks_are_two_entries(self) -> None:
        md = _HELP_BLOCK + "\n```\nusage: t sub\n\noptions:\n  --mode M  m\n```\n"
        self.assertEqual(len(mod.parse_md_help_blocks(md)), 2)


class HelpBlockShapesTest(unittest.TestCase):
    """Which lines of a block are option entries, and which blocks are help blocks."""

    def test_an_epilog_example_is_not_an_option(self) -> None:
        md = (
            "```\nusage: t [--x X]\n\noptions:\n  --x X  x\n\n"
            "examples:\n  --mode run\n\nrun it as:\n  --other now\n```\n"
        )
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_argument_group_sections_are_read(self) -> None:
        md = (
            "```\nusage: t\n\npositional arguments:\n  -  stdin\n\n"
            "Required Arguments:\n  --in IN  in\n\nnamed options:\n  --out O  o\n```\n"
        )
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--in", "--out"})

    def test_a_prompt_line_before_usage_is_accepted(self) -> None:
        for prompt in ("$ python tool.py --help", "> tool -h", "tool.py --help"):
            md = (
                f"```console\n{prompt}\nusage: t [--x X]\n\noptions:\n  --x X  x\n```\n"
            )
            (block,) = mod.parse_md_help_blocks(md)
            self.assertEqual(set(block.options), {"--x"}, prompt)

    def test_a_prompt_line_needs_usage_next(self) -> None:
        md = "```\n$ tool --help\nsee below\n\noptions:\n  --x X  x\n```\n"
        self.assertEqual(mod.parse_md_help_blocks(md), [])

    def test_a_command_not_asking_for_help_is_not_a_prompt(self) -> None:
        md = "```\n$ tool --run\nusage: t\n\noptions:\n  --x X  x\n```\n"
        self.assertEqual(mod.parse_md_help_blocks(md), [])

    def test_a_usage_block_in_a_longer_fence_is_read(self) -> None:
        md = "`````text\nusage: t\n\noptions:\n  --x X  x\n`````\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--x"})

    def test_a_usage_block_shown_inside_another_fence_is_content(self) -> None:
        # A doc showing how to write a help block: the inner fence is the outer one's
        # text, and the outer block does not open with usage:, so nothing is read.
        md = "````markdown\n```text\nusage: t\n\noptions:\n  --x X  x\n```\n````\n"
        self.assertEqual(mod.parse_md_help_blocks(md), [])

    def test_a_declared_group_title_opens_a_section(self) -> None:
        md = (
            "```\nusage: t\n\noptions:\n  --x X  x\n\nOutput:  \n  --out OUT  where\n\n"
            "examples:\n  --mode run\n```\n"
        )
        (block,) = mod.parse_md_help_blocks(md, group_titles=("output",))
        self.assertEqual(set(block.options), {"--x", "--out"})
        (untold,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(untold.options), {"--x"})

    def test_an_equals_spelled_entry_yields_its_name(self) -> None:
        md = "```\nusage: t\n\noptions:\n  --opt=VALUE  o\n```\n"
        (block,) = mod.parse_md_help_blocks(md)
        self.assertEqual(set(block.options), {"--opt"})


class HelpBlockMirrorTest(unittest.TestCase):
    """Check B over a help block: coverage always, defaults only behind run_help."""

    def _tree(self) -> ast.Module:
        return parse_module(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser()
                p.add_argument("-p", "--path", required=True, help="p")
                p.add_argument("--count", "--number", type=int, default=5, help="c")
                p.add_argument("--json", action="store_true", help="j")
                p.add_argument("--mode", help="m")
                return 0
            """
        )

    def _check(
        self, md: str, *, run_help: bool, render: str | None = None
    ) -> tuple[list[mod.Finding], list[Path]]:
        calls: list[Path] = []

        def fake_render(path: Path, _py: str) -> str | None:
            calls.append(path)
            return render

        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("README.md"),
            self._tree(),
            md,
            render_help=fake_render,
            python="py",
            run_help=run_help,
        )
        return findings, calls

    def test_an_option_missing_from_the_block_gates_without_running(self) -> None:
        findings, calls = self._check(_HELP_BLOCK, run_help=False)
        self.assertEqual(calls, [])  # nothing was executed
        gating = [f for f in findings if f.severity == mod.SEVERITY_GATING]
        self.assertEqual([f.context for f in gating], ["--mode"])

    def test_skipped_defaults_are_said_once(self) -> None:
        findings, _calls = self._check(_HELP_BLOCK, run_help=False)
        skipped = [f for f in findings if f.context == "defaults"]
        self.assertEqual(len(skipped), 1)
        self.assertEqual(skipped[0].severity, mod.SEVERITY_ADVISORY)
        self.assertIn("--run-help", skipped[0].message)

    def test_a_block_with_no_defaults_says_nothing_about_them(self) -> None:
        md = "```\nusage: t\n\noptions:\n  --path P  p\n  --count N  c\n  --json  j\n  --mode M  m\n```\n"
        findings, _calls = self._check(md, run_help=False)
        self.assertEqual(findings, [])

    def test_run_help_compares_the_block_default(self) -> None:
        md = _HELP_BLOCK.replace("(default: 5)", "(default: 9)")
        findings, calls = self._check(
            md, run_help=True, render="options:\n  --count COUNT  c (default: 5)\n"
        )
        self.assertEqual(calls, [Path("t.py")])
        wrong = [f for f in findings if f.context == "--count"]
        self.assertEqual(len(wrong), 1)
        self.assertEqual(wrong[0].severity, mod.SEVERITY_GATING)
        self.assertFalse(any(f.context == "defaults" for f in findings))

    def test_run_help_matching_default_is_clean(self) -> None:
        findings, _calls = self._check(
            _HELP_BLOCK,
            run_help=True,
            render="options:\n  --count COUNT  c (default: 5)\n",
        )
        self.assertFalse(any(f.context in ("--count", "defaults") for f in findings))

    def test_a_table_default_column_is_skipped_too(self) -> None:
        md = (
            "| Option | Default |\n| --- | --- |\n| `--path` | — |\n| `--count` | 9 |\n"
        )
        findings, calls = self._check(md, run_help=False)
        self.assertEqual(calls, [])
        self.assertFalse(any(f.context == "--count" for f in findings))
        self.assertEqual(len([f for f in findings if f.context == "defaults"]), 1)

    def test_neither_shape_names_both(self) -> None:
        findings, _calls = self._check("prose only\n", run_help=False)
        (finding,) = findings
        self.assertEqual(finding.context, "arguments table")
        self.assertEqual(finding.severity, mod.SEVERITY_GATING)
        self.assertIn("usage:", finding.message)
        self.assertIn("table", finding.message)

    def test_a_custom_group_pasted_as_argparse_shows_it_gates_nothing(self) -> None:
        tree = parse_module(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser()
                p.add_argument("--path", help="p")
                g = p.add_argument_group("output")
                g.add_argument("--out", help="where")
                h = p.add_argument_group(title="Input", description="d")
                h.add_argument("--src", help="s")
                return 0
            """
        )
        md = (
            "```\nusage: t [--path P] [--out OUT] [--src S]\n\noptions:\n  --path P  p\n\n"
            "output:\n  --out OUT  where\n\nInput:\n  d\n\n  --src S  s\n```\n"
        )
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("README.md"),
            tree,
            md,
            render_help=lambda _p, _py: None,
            python="py",
        )
        self.assertEqual(findings, [])

    def test_a_table_and_a_block_cover_together(self) -> None:
        md = (
            "| Option |\n| --- |\n| `--path P` |\n| `--json` |\n\n"
            "```\nusage: t\n\noptions:\n  --count N  c\n  --mode M  m\n```\n"
        )
        findings, _calls = self._check(md, run_help=False)
        self.assertEqual(findings, [])

    def test_messages_name_the_doc_not_an_md(self) -> None:
        md = _HELP_BLOCK.replace("(default: 5)", "(default: 9)") + (
            "\n| Option |\n| --- |\n| `--ghost` |\n"
        )
        findings, _calls = self._check(
            md, run_help=True, render="options:\n  --count COUNT  c (default: 5)\n"
        )
        texts = [f.message + f.fix for f in findings]
        self.assertTrue(any("--ghost" in t for t in texts))
        self.assertTrue(any("--count" in t for t in texts))
        self.assertFalse([t for t in texts if ".md" in t])

    def test_missing_doc_message_points_at_the_doc_option(self) -> None:
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._tree(),
            None,
            render_help=lambda _p, _py: None,
            python="py",
        )
        self.assertIn("--doc", findings[0].message)
        self.assertIn("no companion doc", findings[0].message)


if __name__ == "__main__":
    unittest.main()
