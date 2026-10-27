"""Grouped suite: reading each side of the comparison into a structure.

Nothing here compares anything. Rendered --help text becomes a defaults mapping, the
companion .md's tables become MdArgTable, an .md section becomes a set of exit codes, and
the module's own AST becomes the same -- four parsers over four inputs. A failure points at
a parser, not at a rule, which is exactly why the rules that consume these structures live
in the next module rather than this one.
"""

from __future__ import annotations

import sys
import textwrap
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import first_call, first_expr_value, parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir


class HelpRenderParseTest(unittest.TestCase):
    HELP = textwrap.dedent(
        """\
        usage: t.py [-h] --path PATH

        do a thing

        options:
          -h, --help            show this help message and exit
          --path PATH           the path
          --count COUNT         how many
                                to run (default: 5)
          --mode MODE           the mode (default: fast)

        epilog line not indented
        """
    )

    def test_options_block_start(self) -> None:
        self.assertEqual(mod._options_block_start(["a", "options:", "b"]), 1)
        self.assertEqual(mod._options_block_start(["optional arguments:", "b"]), 0)
        self.assertIsNone(mod._options_block_start(["no", "block"]))

    def test_parse_help_defaults(self) -> None:
        defaults = mod.parse_help_defaults(self.HELP)
        self.assertEqual(defaults["--count"], "5")  # wrapped line joined
        self.assertEqual(defaults["--mode"], "fast")
        self.assertNotIn("--path", defaults)  # no rendered default

    def test_parse_help_defaults_no_block(self) -> None:
        self.assertEqual(mod.parse_help_defaults("no options here"), {})

    def test_iter_help_entries_orphan_continuation(self) -> None:
        # A continuation-indented first line with no preceding option start.
        self.assertEqual(list(mod._iter_help_entries(["      orphan"])), [])

    def test_iter_help_entries_breaks_on_unindented(self) -> None:
        entries = list(mod._iter_help_entries(["  --a A  help", "unindented"]))
        self.assertEqual(entries, ["--a A  help"])


class MdTableParseTest(unittest.TestCase):
    def test_split_row_escaped_pipe(self) -> None:
        self.assertEqual(
            mod._split_row("| `--p <a\\|b>` | yes | x |"), ["`--p <a|b>`", "yes", "x"]
        )

    def test_is_table_row(self) -> None:
        self.assertTrue(mod._is_table_row("  | a |"))
        self.assertFalse(mod._is_table_row("plain"))

    def test_is_separator_cells(self) -> None:
        self.assertTrue(mod._is_separator_cells(["---", ":--:"]))
        self.assertFalse(mod._is_separator_cells(["Argument", "---"]))

    def test_is_arg_header(self) -> None:
        for good in ("Argument", "Options", "`Flag`"):
            self.assertTrue(mod._is_arg_header(good))
        self.assertFalse(mod._is_arg_header("Meaning"))

    def test_default_column_index(self) -> None:
        self.assertEqual(mod._default_column_index(["Option", "Default", "M"]), 1)
        self.assertIsNone(mod._default_column_index(["Option", "Meaning"]))

    def test_build_arg_table_default_and_short_row(self) -> None:
        header = ["Option", "Default", "Meaning"]
        data = [
            ["`--a A`", "5", "m"],
            ["`--b`, `--c`"],
        ]  # second row shorter than default col
        table = mod._build_arg_table(header, data)
        self.assertTrue(table.has_default_column)
        self.assertEqual(table.options["--a"], "5")
        self.assertIsNone(table.options["--b"])  # default_col >= len(row)
        self.assertIsNone(table.options["--c"])

    def _tables(self, md: str) -> list[mod.MdArgTable]:
        return mod.parse_md_arg_tables(textwrap.dedent(md))

    def test_parse_finds_arg_table(self) -> None:
        tables = self._tables(
            """
            | Option | Meaning |
            | --- | --- |
            | `--path PATH` | p |
            """
        )
        self.assertEqual(len(tables), 1)
        self.assertIn("--path", tables[0].options)

    def test_parse_skips_nonarg_table_then_finds_arg_table(self) -> None:
        tables = self._tables(
            """
            | Check | What |
            | --- | --- |
            | A | x |

            | Argument | Meaning |
            | --- | --- |
            | `--real R` | r |
            """
        )
        self.assertEqual(len(tables), 1)
        self.assertEqual(set(tables[0].options), {"--real"})

    def test_parse_multiple_arg_tables_for_subparsers(self) -> None:
        # A subparser tool documents its options across several per-subcommand tables.
        tables = self._tables(
            """
            ### setup arguments

            | Option | Meaning |
            | --- | --- |
            | `--pat T` | p |
            | `--server-url U` | s |

            ### create-user arguments

            | Option | Meaning |
            | --- | --- |
            | `--pat T` | p |
            | `--user-email E` | e |
            """
        )
        self.assertEqual(len(tables), 2)
        self.assertEqual(
            mod._documented_names(tables), {"--pat", "--server-url", "--user-email"}
        )

    def test_parse_no_arg_table(self) -> None:
        tables = self._tables(
            """
            | Check | What |
            | --- | --- |
            | A | x |
            """
        )
        self.assertEqual(tables, [])

    def test_parse_lone_pipe_line_and_non_separator(self) -> None:
        # A '|' row not followed by a separator; then a lone '|' row at the end.
        self.assertEqual(self._tables("| a | b |\n| c | d |\nprose\n|only|"), [])

    def test_documented_names_discards_help(self) -> None:
        table = mod.MdArgTable(
            {"--path": None, "--help": None}, has_default_column=False
        )
        self.assertEqual(mod._documented_names([table]), {"--path"})

    def test_option_default_cell_unambiguous_and_ambiguous(self) -> None:
        one = mod.MdArgTable({"--n": "5"}, has_default_column=True)
        two = mod.MdArgTable({"--n": "5"}, has_default_column=True)
        no_col = mod.MdArgTable({"--m": None}, has_default_column=False)
        self.assertEqual(mod._option_default_cell([one], "--n"), (True, "5"))
        # documented in two default-column tables -> ambiguous -> not checkable.
        self.assertEqual(mod._option_default_cell([one, two], "--n"), (False, None))
        # only in a table without a default column -> nothing to check.
        self.assertEqual(mod._option_default_cell([no_col], "--m"), (False, None))


class SectionAndExitCodesTest(unittest.TestCase):
    def test_section_after_heading_stops_at_next(self) -> None:
        md = "## Exit codes\n- `0` clean\n- `1` bad\n## Next\nother\n"
        section = mod._section_after_heading(md, r"exit codes?")
        assert section is not None
        self.assertIn("`0`", section)
        self.assertNotIn("other", section)

    def test_section_runs_to_end(self) -> None:
        md = "## Exit codes\n- `0` clean\n"
        self.assertIn("`0`", mod._section_after_heading(md, r"exit codes?") or "")

    def test_section_absent(self) -> None:
        self.assertIsNone(mod._section_after_heading("no heading", r"exit codes?"))

    def test_parse_md_exit_codes_backtick(self) -> None:
        self.assertEqual(
            mod.parse_md_exit_codes("## Exit codes\n- `0` ok\n- `2` bad\n"), {0, 2}
        )

    def test_parse_md_exit_codes_exit_word(self) -> None:
        self.assertEqual(
            mod.parse_md_exit_codes("## Exit codes\nexit 1 means fail\n"), {1}
        )

    def test_parse_md_exit_codes_section_without_codes(self) -> None:
        self.assertIsNone(mod.parse_md_exit_codes("## Exit codes\nno digits here\n"))

    def test_parse_md_exit_codes_no_section(self) -> None:
        self.assertIsNone(mod.parse_md_exit_codes("Exit `0` in prose, no section.\n"))


class AstExitCodesTest(unittest.TestCase):
    def test_is_sys_exit(self) -> None:
        self.assertTrue(mod._is_sys_exit(first_call("sys.exit(1)").func))
        self.assertFalse(mod._is_sys_exit(first_call("os.exit(1)").func))
        self.assertFalse(mod._is_sys_exit(first_call("exit(1)").func))

    def test_int_constants_excludes_bool(self) -> None:
        self.assertEqual(mod._int_constants(first_expr_value("[0, 2, True]")), {0, 2})

    def test_ast_exit_codes_from_main_and_sysexit(self) -> None:
        tree = parse_module(
            """
            import sys
            def main():
                if x:
                    return 2
                return 1 if y else 0
            sys.exit(main())
            """
        )
        self.assertEqual(mod.ast_exit_codes(tree), {0, 1, 2})

    def test_ast_exit_codes_no_main(self) -> None:
        self.assertEqual(mod.ast_exit_codes(parse_module("sys.exit(3)")), {3})
