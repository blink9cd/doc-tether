"""Grouped suite: the CLI side, meaning what the argparse tree says.

Recognizing a CLI module, lifting each add_argument call into an OptionInfo, and reading a
help= literal; then the two checks that judge what comes out of that walk: Check A (a
hardcoded default in help= text argparse would have appended itself) and Check D (a
positional where a named option belongs). They group together because they fail for one
reason: the AST walk read the parser wrong. A reader holding a wrong OptionInfo needs the
check that consumed it in the same file.
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


class IsCliModuleTest(unittest.TestCase):
    def test_attribute_form(self) -> None:
        self.assertTrue(
            mod.is_cli_module(
                parse_module("import argparse\nargparse.ArgumentParser()")
            )
        )

    def test_bare_name_form(self) -> None:
        self.assertTrue(
            mod.is_cli_module(
                parse_module("from argparse import ArgumentParser\nArgumentParser()")
            )
        )

    def test_a_subclass_named_for_the_parser_is_a_cli(self) -> None:
        # stdio_utf8's escaping parser is the one its companion doc tells every tool to
        # adopt: missing it would silently drop each adopting tool from this gate.
        for src in (
            "import stdio_utf8\nstdio_utf8.EscapingArgumentParser()",
            "from stdio_utf8 import EscapingArgumentParser\nEscapingArgumentParser()",
        ):
            with self.subTest(src=src):
                self.assertTrue(mod.is_cli_module(parse_module(src)))

    def test_other_calls_are_not_cli(self) -> None:
        # A Call whose func is a non-matching Attribute and a non-matching Name.
        self.assertFalse(mod.is_cli_module(parse_module("x.foo()\nprint('hi')")))

    def test_a_name_only_containing_the_parser_is_not_cli(self) -> None:
        # The name must end with it: a factory is not a parser.
        for src in ("f.ArgumentParserFactory()", "ArgumentParserFactory()"):
            with self.subTest(src=src):
                self.assertFalse(mod.is_cli_module(parse_module(src)))

    def test_no_calls_at_all(self) -> None:
        self.assertFalse(mod.is_cli_module(parse_module("y = 1\n")))


class CliOptionsFromAstTest(unittest.TestCase):
    def _opts(self, body: str) -> dict[str, mod.OptionInfo]:
        src = "import argparse\np = argparse.ArgumentParser()\n" + textwrap.dedent(body)
        return {o.name: o for o in mod.cli_options_from_ast(ast.parse(src))}

    def test_long_option_captured(self) -> None:
        opts = self._opts('p.add_argument("--path", required=True, help="h")\n')
        self.assertIn("--path", opts)
        self.assertTrue(opts["--path"].required)

    def test_positional_and_short_only_skipped(self) -> None:
        opts = self._opts('p.add_argument("pos")\np.add_argument("-v")\n')
        self.assertEqual(opts, {})

    def test_help_option_skipped(self) -> None:
        opts = self._opts('p.add_argument("--help", action="help")\n')
        self.assertEqual(opts, {})

    def test_store_true_flag(self) -> None:
        opts = self._opts('p.add_argument("--json", action="store_true")\n')
        self.assertTrue(opts["--json"].is_flag)

    def test_literal_int_default(self) -> None:
        opts = self._opts('p.add_argument("--n", default=5)\n')
        self.assertTrue(opts["--n"].has_literal_default)
        self.assertEqual(opts["--n"].literal_default, 5)

    def test_literal_list_default(self) -> None:
        opts = self._opts('p.add_argument("--x", default=[], action="append")\n')
        self.assertEqual(opts["--x"].literal_default, [])

    def test_list_with_nonconstant_is_dynamic(self) -> None:
        opts = self._opts('p.add_argument("--x", default=[foo])\n')
        self.assertFalse(opts["--x"].has_literal_default)

    def test_dynamic_default_and_nonliteral_action(self) -> None:
        opts = self._opts('p.add_argument("--x", default=f(), action=act)\n')
        self.assertFalse(opts["--x"].has_literal_default)
        self.assertFalse(opts["--x"].is_flag)

    def test_suppressed_help_is_hidden(self) -> None:
        opts = self._opts('p.add_argument("--secret", help=argparse.SUPPRESS)\n')
        self.assertTrue(opts["--secret"].hidden)

    def test_bare_suppress_name_is_hidden(self) -> None:
        opts = self._opts('p.add_argument("--secret", help=SUPPRESS)\n')
        self.assertTrue(opts["--secret"].hidden)


class ArgumentGroupTitlesTest(unittest.TestCase):
    def test_literal_titles_positional_and_keyword(self) -> None:
        tree = parse_module(
            """
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument_group("output")
            p.add_argument_group(title="Input", description="d")
            """
        )
        self.assertEqual(mod.argument_group_titles(tree), {"output", "Input"})

    def test_a_non_literal_or_absent_title_is_skipped(self) -> None:
        tree = parse_module(
            """
            import argparse
            p = argparse.ArgumentParser()
            p.add_argument_group(NAME)
            p.add_argument_group(title=f"{x}")
            p.add_argument_group()
            p.add_argument_group(5)
            """
        )
        self.assertEqual(mod.argument_group_titles(tree), set())


class HelpLiteralTextTest(unittest.TestCase):
    def _help_node(self, src: str) -> ast.expr | None:
        node = parse_module(
            "import argparse\np=argparse.ArgumentParser()\n" + src
        ).body[-1]
        assert isinstance(node, ast.Expr)
        call = node.value
        assert isinstance(call, ast.Call)
        return {kw.arg: kw.value for kw in call.keywords}.get("help")

    def test_plain_string(self) -> None:
        self.assertEqual(
            mod.help_literal_text(self._help_node('p.add_argument("--x", help="hi")')),
            "hi",
        )

    def test_fstring_literal_parts(self) -> None:
        node = self._help_node(
            'p.add_argument("--x", help=f"count {n} times (default: 5)")'
        )
        text = mod.help_literal_text(node)
        assert text is not None
        self.assertIn("(default: 5)", text)

    def test_variable_help_is_none(self) -> None:
        self.assertIsNone(
            mod.help_literal_text(self._help_node('p.add_argument("--x", help=HELP)'))
        )

    def test_absent_help_is_none(self) -> None:
        self.assertIsNone(mod.help_literal_text(None))


class CheckHardcodedDefaultsTest(unittest.TestCase):
    def _findings(self, body: str) -> list[mod.Finding]:
        src = "import argparse\np=argparse.ArgumentParser()\n" + textwrap.dedent(body)
        return mod.check_hardcoded_defaults(ast.parse(src), Path("t.py"))

    def test_flags_hardcoded_default(self) -> None:
        findings = self._findings('p.add_argument("--n", help="runs (default: 5)")\n')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check, mod.CHECK_A)
        self.assertEqual(findings[0].context, "--n")

    def test_percent_default_token_is_clean(self) -> None:
        self.assertEqual(
            self._findings('p.add_argument("--n", help="runs %(default)s")\n'), []
        )

    def test_no_default_marker_is_clean(self) -> None:
        self.assertEqual(
            self._findings('p.add_argument("--n", help="just runs")\n'), []
        )

    def test_nonliteral_help_skipped(self) -> None:
        self.assertEqual(self._findings('p.add_argument("--n", help=HELP)\n'), [])

    def test_no_help_kwarg_skipped(self) -> None:
        self.assertEqual(self._findings('p.add_argument("--n")\n'), [])

    def test_positional_uses_placeholder_context(self) -> None:
        findings = self._findings('p.add_argument("pos", help="x (default: 1)")\n')
        self.assertEqual(findings[0].context, "<arg>")


class CheckPositionalArgumentsTest(unittest.TestCase):
    """Check D: user-facing CLI args must be named options, never positionals."""

    def _findings(self, body: str) -> list[mod.Finding]:
        src = "import argparse\np=argparse.ArgumentParser()\n" + textwrap.dedent(body)
        return mod.check_positional_arguments(ast.parse(src), Path("t.py"))

    def test_positional_is_flagged(self) -> None:
        findings = self._findings('p.add_argument("value")\n')
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check, mod.CHECK_D)
        self.assertEqual(findings[0].severity, mod.SEVERITY_GATING)
        self.assertEqual(findings[0].context, "value")

    def test_long_option_not_flagged(self) -> None:
        self.assertEqual(
            self._findings('p.add_argument("--value", required=True)\n'), []
        )

    def test_short_option_not_flagged(self) -> None:
        self.assertEqual(self._findings('p.add_argument("-v")\n'), [])

    def test_nonliteral_first_arg_skipped(self) -> None:
        # A variable/expr first arg cannot be statically proven positional.
        self.assertEqual(self._findings("p.add_argument(name)\n"), [])

    def test_nonstring_constant_first_arg_skipped(self) -> None:
        # A non-string constant first arg (pathological) is not a positional name.
        self.assertEqual(self._findings("p.add_argument(5)\n"), [])

    def test_keyword_only_call_skipped(self) -> None:
        # No positional args at all (e.g. add_argument(dest=...)) -> nothing to check.
        self.assertEqual(self._findings('p.add_argument(dest="x")\n'), [])

    def test_add_subparsers_not_caught(self) -> None:
        # add_subparsers is a different call; Check D must not flag it.
        self.assertEqual(self._findings('p.add_subparsers(dest="cmd")\n'), [])

    def test_add_parser_positional_not_caught(self) -> None:
        # subs.add_parser("setup") is not add_argument -> not a Check D subject.
        src = (
            "import argparse\np=argparse.ArgumentParser()\n"
            's=p.add_subparsers()\ns.add_parser("setup")\n'
        )
        self.assertEqual(
            mod.check_positional_arguments(ast.parse(src), Path("t.py")), []
        )
