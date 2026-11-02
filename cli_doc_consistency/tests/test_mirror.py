"""Grouped suite: Check B, whether the .md mirrors the CLI, and what is exempt.

The finding rules over already-parsed structures: an option documented or missing, a
documented default contradicting the real one, an exit code raised but undocumented. The
numeric layer sits here too, since deciding whether two defaults disagree is what the
contradiction rule is made of. check_md_mirror closes the module because it sequences those
rules and owns the gating-versus-advisory call: the exemptions are as much of this check
as the findings are.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir


class NumericDefaultTest(unittest.TestCase):
    def test_cell_number(self) -> None:
        self.assertEqual(mod._cell_number("100"), 100.0)
        self.assertEqual(mod._cell_number("`100`"), 100.0)
        self.assertEqual(mod._cell_number("50%"), 50.0)
        self.assertEqual(mod._cell_number("600.0"), 600.0)
        self.assertIsNone(mod._cell_number("derived"))
        self.assertIsNone(mod._cell_number("[]"))

    def _opt(self, **kw: object) -> mod.OptionInfo:
        base = dict(
            name="--n",
            required=False,
            is_flag=False,
            has_literal_default=False,
            literal_default=None,
            hidden=False,
            lineno=1,
        )
        base.update(kw)
        return mod.OptionInfo(**base)  # type: ignore[arg-type]

    def test_option_default_number_from_render(self) -> None:
        self.assertEqual(mod._option_default_number(self._opt(), {"--n": "7"}), 7.0)

    def test_option_default_number_render_nonnumeric(self) -> None:
        self.assertIsNone(mod._option_default_number(self._opt(), {"--n": "fast"}))

    def test_option_default_number_from_literal(self) -> None:
        opt = self._opt(has_literal_default=True, literal_default=9)
        self.assertEqual(mod._option_default_number(opt, {}), 9.0)

    def test_option_default_number_bool_literal_is_none(self) -> None:
        opt = self._opt(has_literal_default=True, literal_default=True)
        self.assertIsNone(mod._option_default_number(opt, {}))

    def test_option_default_number_no_default(self) -> None:
        self.assertIsNone(mod._option_default_number(self._opt(), {}))

    def test_number_str(self) -> None:
        self.assertEqual(mod._number_str(5.0), "5")
        self.assertEqual(mod._number_str(2.5), "2.5")


class CoverageAndDefaultFindingsTest(unittest.TestCase):
    def _opt(self, name: str, **kw: object) -> mod.OptionInfo:
        base = dict(
            name=name,
            required=False,
            is_flag=False,
            has_literal_default=False,
            literal_default=None,
            hidden=False,
            lineno=1,
        )
        base.update(kw)
        return mod.OptionInfo(**base)  # type: ignore[arg-type]

    def test_coverage_missing_and_extra_and_hidden(self) -> None:
        options = [
            self._opt("--kept"),
            self._opt("--missing"),
            self._opt("--secret", hidden=True),
        ]
        # Two arg tables (subparser-style); coverage matches against their union.
        tables = [
            mod.MdArgTable({"--kept": None, "--help": None}, has_default_column=False),
            mod.MdArgTable({"--ghost": None}, has_default_column=False),
        ]
        findings = mod._coverage_findings(options, tables, Path("t.md"))
        contexts = sorted(f.context for f in findings)
        # --missing (in CLI not md) and --ghost (in md not CLI); --secret hidden,
        # --kept documented, --help discarded.
        self.assertEqual(contexts, ["--ghost", "--missing"])

    def test_default_contradiction_flagged(self) -> None:
        options = [self._opt("--n", has_literal_default=True, literal_default=5)]
        tables = [mod.MdArgTable({"--n": "9"}, has_default_column=True)]
        findings = mod._default_findings(options, tables, {}, Path("t.md"))
        self.assertEqual(len(findings), 1)
        self.assertIn("9", findings[0].message)

    def test_default_match_no_finding(self) -> None:
        options = [self._opt("--n", has_literal_default=True, literal_default=5)]
        tables = [mod.MdArgTable({"--n": "5"}, has_default_column=True)]
        self.assertEqual(mod._default_findings(options, tables, {}, Path("t.md")), [])

    def test_default_ambiguous_across_subcommand_tables_skipped(self) -> None:
        # --n documented (with a default) in two subcommand tables -> ambiguous ->
        # never flagged, even though one cell would otherwise contradict.
        options = [self._opt("--n", has_literal_default=True, literal_default=5)]
        tables = [
            mod.MdArgTable({"--n": "5"}, has_default_column=True),
            mod.MdArgTable({"--n": "9"}, has_default_column=True),
        ]
        self.assertEqual(mod._default_findings(options, tables, {}, Path("t.md")), [])

    def test_default_skips_required_flag_hidden(self) -> None:
        options = [
            self._opt("--r", required=True),
            self._opt("--f", is_flag=True),
            self._opt("--h", hidden=True),
        ]
        tables = [
            mod.MdArgTable(
                {"--r": "1", "--f": "1", "--h": "1"}, has_default_column=True
            )
        ]
        self.assertEqual(mod._default_findings(options, tables, {}, Path("t.md")), [])

    def test_default_skips_when_actual_or_cell_or_documented_none(self) -> None:
        options = [
            self._opt("--nonum"),  # no default -> actual None
            self._opt(
                "--absent", has_literal_default=True, literal_default=5
            ),  # not in table
            self._opt(
                "--prose", has_literal_default=True, literal_default=5
            ),  # prose cell
        ]
        tables = [mod.MdArgTable({"--prose": "derived"}, has_default_column=True)]
        self.assertEqual(mod._default_findings(options, tables, {}, Path("t.md")), [])


class ExitCodeFindingsTest(unittest.TestCase):
    def test_no_section_yields_nothing(self) -> None:
        tree = parse_module("def main():\n    return 0\n")
        self.assertEqual(mod._exit_code_findings(tree, "no section", Path("t.md")), [])

    def test_undocumented_code_flagged(self) -> None:
        tree = parse_module("def main():\n    return 2\n")
        findings = mod._exit_code_findings(
            tree, "## Exit codes\n- `0` ok\n", Path("t.md")
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].severity, mod.SEVERITY_ADVISORY)
        self.assertIn("2", findings[0].message)

    def test_all_documented_yields_nothing(self) -> None:
        tree = parse_module("def main():\n    return 0\n")
        self.assertEqual(
            mod._exit_code_findings(tree, "## Exit codes\n- `0` ok\n", Path("t.md")), []
        )


class CheckMdMirrorTest(unittest.TestCase):
    def _cli_tree(self) -> ast.Module:
        return parse_module(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser()
                p.add_argument("--path", required=True, help="p")
                p.add_argument("--count", default=5, help="c")
                return 0
            """
        )

    def test_missing_md_gates_by_default(self) -> None:
        # A discovered argparse CLI with no companion .md hard-fails.
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._cli_tree(),
            None,
            render_help=lambda _p, _py: None,
            python="py",
        )
        self.assertEqual([f.severity for f in findings], [mod.SEVERITY_GATING])
        self.assertEqual(findings[0].check, mod.CHECK_B)

    def test_missing_md_advisory_when_allowed(self) -> None:
        # --allow-missing-doc downgrades it to advisory, which never gates.
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._cli_tree(),
            None,
            render_help=lambda _p, _py: None,
            python="py",
            allow_missing_doc=True,
        )
        self.assertEqual([f.severity for f in findings], [mod.SEVERITY_ADVISORY])

    def test_no_option_surface_gates(self) -> None:
        # A doc with neither a table nor a pasted --help block verifies no coverage at
        # all, so it gates rather than advising.
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._cli_tree(),
            "no table here\n",
            render_help=lambda _p, _py: None,
            python="py",
        )
        (finding,) = [f for f in findings if f.context == "arguments table"]
        self.assertEqual(finding.severity, mod.SEVERITY_GATING)

    def test_coverage_gating_when_table_present(self) -> None:
        md = "| Option | Meaning |\n| --- | --- |\n| `--path P` | p |\n"
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._cli_tree(),
            md,
            render_help=lambda _p, _py: None,
            python="py",
        )
        # --count is missing from the md table.
        self.assertTrue(
            any(
                f.context == "--count" and f.severity == mod.SEVERITY_GATING
                for f in findings
            )
        )

    def test_subparser_options_across_tables_no_false_positive(self) -> None:
        # A subparser tool: options split across two per-subcommand tables, one
        # option (--path) repeated. Every CLI option is covered by the union.
        tree = parse_module(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser()
                subs = p.add_subparsers()
                a = subs.add_parser("setup")
                a.add_argument("--path", required=True, help="p")
                a.add_argument("--count", default=5, help="c")
                b = subs.add_parser("run")
                b.add_argument("--path", required=True, help="p")
                b.add_argument("--mode", help="m")
                return 0
            """
        )
        md = (
            "### setup arguments\n\n"
            "| Option | Meaning |\n| --- | --- |\n"
            "| `--path P` | p |\n| `--count N` | c |\n\n"
            "### run arguments\n\n"
            "| Option | Meaning |\n| --- | --- |\n"
            "| `--path P` | p |\n| `--mode M` | m |\n"
        )
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            tree,
            md,
            render_help=lambda _p, _py: None,
            python="py",
        )
        self.assertEqual([f for f in findings if f.severity == mod.SEVERITY_GATING], [])

    def test_default_column_invokes_render(self) -> None:
        md = "| Option | Default | Meaning |\n| --- | --- | --- |\n| `--path P` | — | p |\n| `--count N` | 9 | c |\n"
        calls: list[Path] = []

        def fake_render(path: Path, _py: str) -> str:
            calls.append(path)
            return "options:\n  --count COUNT  c (default: 5)\n"

        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._cli_tree(),
            md,
            render_help=fake_render,
            python="py",
            run_help=True,
        )
        self.assertEqual(calls, [Path("t.py")])  # render was used
        # Rendered default 5 contradicts documented 9 -> a gating default finding.
        self.assertTrue(
            any(
                f.context == "--count" and f.severity == mod.SEVERITY_GATING
                for f in findings
            )
        )

    def test_default_column_render_unavailable_falls_back(self) -> None:
        md = "| Option | Default | Meaning |\n| --- | --- | --- |\n| `--path P` | — | p |\n| `--count N` | 5 | c |\n"
        findings = mod.check_md_mirror(
            Path("t.py"),
            Path("t.md"),
            self._cli_tree(),
            md,
            render_help=lambda _p, _py: None,
            python="py",
            run_help=True,
        )
        # AST literal default 5 matches documented 5 -> no default finding.
        self.assertFalse(any(f.context == "--count" for f in findings))
