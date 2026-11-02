"""Grouped suite: the tool's own outward surface.

What a caller or a gate sees, as opposed to what any one check computes: analyze_file
threading the three checks over a single file, the text and JSON reports, the parser itself
(including that it is wired to the shared formatter rather than re-implementing it), and
main's argv handling and exit codes.
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above
import help_formatter  # noqa: E402 - same bootstrap
from _fixtures import parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir


class FormatterWiringTest(unittest.TestCase):
    """Assert the parser is wired to the shared formatter, not that it re-implements it.

    Why this is not a rendering assertion for the required options:
    ``help_formatter.md``, "The version trap".
    """

    def test_parser_uses_the_shared_formatter(self) -> None:
        formatter_class = mod.build_parser().formatter_class
        self.assertIs(formatter_class, help_formatter.UnsetHidingFormatter)


class HelpRendersNoUnsetDefaultTest(unittest.TestCase):
    """``--help`` shows a default only where one is set.

    A flag left out, ``--doc`` left out and ``--exclude`` given no value hold nothing a
    person could type, so ``(default: False)``, ``(default: None)`` and ``(default: [])``
    would say nothing true. This one is a rendering assertion on purpose: unlike the
    required-option case, it holds on every interpreter.
    """

    def test_help_prints_no_unset_default(self) -> None:
        # Collapse the wrap: argparse may split "(default:" from its value.
        rendered = " ".join(mod.build_parser().format_help().split())
        for unset in ("(default: False)", "(default: None)", "(default: [])"):
            with self.subTest(unset=unset):
                self.assertNotIn(unset, rendered)

    def test_unset_flags_read_none(self) -> None:
        args = mod.build_parser().parse_args(["--path", "x"])
        for flag in ("json", "allow_missing_doc", "run_help", "no_run_help"):
            with self.subTest(flag=flag):
                self.assertIsNone(getattr(args, flag))
        self.assertIsNone(args.check_positionals)

    def test_unset_flags_still_yield_boolean_switches(self) -> None:
        # The opt-ins combine a None flag with the config's bool: the result stays a bool.
        args = mod.build_parser().parse_args(["--path", "x"])
        with tempfile.TemporaryDirectory() as scratch:
            switches = mod._effective_switches(args, Path(scratch))
        self.assertIs(switches.run_help, False)
        self.assertIs(switches.check_positionals, False)


class ReportTest(unittest.TestCase):
    def _results(self) -> dict[Path, list[mod.Finding]]:
        return {
            Path("a.py"): [
                mod.Finding(
                    mod.CHECK_A, mod.SEVERITY_GATING, "a.py", 3, "--n", "m", "fx"
                ),
                mod.Finding(
                    mod.CHECK_B, mod.SEVERITY_ADVISORY, "a.md", None, "x", "adv", "fx2"
                ),
            ]
        }

    def test_verdict_name(self) -> None:
        self.assertEqual(
            mod._verdict_name("cli_doc_consistency.py"), "CLI_DOC_CONSISTENCY"
        )

    def test_format_finding_with_and_without_line(self) -> None:
        with_line = mod.Finding(
            mod.CHECK_A, mod.SEVERITY_GATING, "a.py", 3, "--n", "m", "fx"
        )
        without = mod.Finding(
            mod.CHECK_B, mod.SEVERITY_GATING, "a.md", None, "x", "m2", "fx2"
        )
        self.assertIn("a.py:3", mod._format_finding(with_line))
        self.assertIn("fix: fx", mod._format_finding(with_line))
        text = mod._format_finding(without)
        self.assertIn("a.md  [", text)
        self.assertNotIn("a.md:", text)

    def test_text_report_gating_and_advisory(self) -> None:
        text = mod.format_report(
            self._results(), "cli_doc_consistency.py", as_json=False
        )
        self.assertIn("===== a.py =====", text)
        self.assertIn("Advisory (non-gating):", text)
        self.assertIn("FINAL RESULT: FAIL", text)

    def test_text_report_clean(self) -> None:
        text = mod.format_report(
            {Path("a.py"): []}, "cli_doc_consistency.py", as_json=False
        )
        self.assertIn("FINAL RESULT: PASS", text)
        self.assertIn("0 gating finding(s)", text)

    def test_json_report(self) -> None:
        data = json.loads(mod.format_report(self._results(), "prog.py", as_json=True))
        self.assertEqual(data["gating"], 1)
        self.assertEqual(data["advisory"], 1)
        self.assertFalse(data["passed"])
        self.assertIn("a.py", data["files"])

    def test_build_parser_smoke(self) -> None:
        args = mod.build_parser().parse_args(["--path", "x", "--json"])
        self.assertEqual(args.path, "x")
        self.assertTrue(args.json)

    def test_build_parser_allow_missing_doc(self) -> None:
        default = mod.build_parser().parse_args(["--path", "x"])
        self.assertFalse(default.allow_missing_doc)  # default off (gates)
        opted = mod.build_parser().parse_args(["--path", "x", "--allow-missing-doc"])
        self.assertTrue(opted.allow_missing_doc)


class AnalyzeFileTest(unittest.TestCase):
    def test_all_three_checks_combined(self) -> None:
        tree = parse_module(
            """
            \"\"\"Needs 3.10+.\"\"\"
            import argparse
            import tomllib
            def main():
                p = argparse.ArgumentParser()
                p.add_argument("--n", help="runs (default: 5)")
                return 0
            """
        )
        findings = mod.analyze_file(
            Path("t.py"),
            Path("t.md"),
            tree,
            "no table\n",
            render_help=lambda _p, _py: None,
            python="py",
        )
        checks = {f.check for f in findings}
        self.assertEqual(checks, {mod.CHECK_A, mod.CHECK_B, mod.CHECK_C})

    def test_positional_check_wired(self) -> None:
        # Check D is run by analyze_file: a positional arg surfaces a CHECK_D finding.
        tree = parse_module(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser()
                p.add_argument("target")
                return 0
            """
        )
        findings = mod.analyze_file(
            Path("t.py"),
            Path("t.md"),
            tree,
            "| Option | Meaning |\n| --- | --- |\n",
            render_help=lambda _p, _py: None,
            python="py",
            check_positionals=True,
        )
        self.assertTrue(any(f.check == mod.CHECK_D for f in findings))

    def test_missing_doc_advisory_threads_through(self) -> None:
        # analyze_file forwards allow_missing_doc into Check B's missing-.md finding.
        tree = parse_module(
            """
            import argparse
            def main():
                p = argparse.ArgumentParser()
                p.add_argument("--path", required=True, help="p")
                return 0
            """
        )
        findings = mod.analyze_file(
            Path("t.py"),
            Path("t.md"),
            tree,
            None,
            render_help=lambda _p, _py: None,
            python="py",
            allow_missing_doc=True,
        )
        self.assertEqual([f.severity for f in findings], [mod.SEVERITY_ADVISORY])


class MainTest(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(args)
        return code, out.getvalue(), err.getvalue()

    def _tool_dir(self, py: str, md: str | None) -> Path:
        root = Path(tempfile.mkdtemp())
        (root / "pyproject.toml").write_text(
            "[tool.other]\n", encoding="utf-8", newline="\n"
        )
        (root / "tool.py").write_text(
            textwrap.dedent(py), encoding="utf-8", newline="\n"
        )
        if md is not None:
            (root / "tool.md").write_text(md, encoding="utf-8", newline="\n")
        return root

    _CLEAN_PY = """\
        import argparse
        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--path", required=True, help="p")
            return 0
        """
    _CLEAN_MD = "| Option | Meaning |\n| --- | --- |\n| `--path P` | p |\n"

    def test_missing_path(self) -> None:
        code, _o, err = self._run(["--path", "nope/x.py"])
        self.assertEqual(code, 2)
        self.assertIn("does not exist", err)

    def test_non_py_file(self) -> None:
        root = Path(tempfile.mkdtemp())
        f = root / "readme.txt"
        f.write_text("x", encoding="utf-8", newline="\n")
        code, _o, err = self._run(["--path", str(f)])
        self.assertEqual(code, 2)
        self.assertIn("not a Python file", err)

    def test_malformed_config(self) -> None:
        root = Path(tempfile.mkdtemp())
        (root / "pyproject.toml").write_text(
            "[tool.cli_doc_consistency\n", encoding="utf-8", newline="\n"
        )
        (root / "tool.py").write_text(
            "import argparse\nargparse.ArgumentParser()\n",
            encoding="utf-8",
            newline="\n",
        )
        code, _o, err = self._run(["--path", str(root)])
        self.assertEqual(code, 2)
        self.assertIn("ERROR:", err)

    def test_named_file_not_cli(self) -> None:
        root = self._tool_dir("x = 1\n", None)
        code, _o, err = self._run(["--path", str(root / "tool.py")])
        self.assertEqual(code, 2)
        self.assertIn("not an argparse CLI", err)

    def test_unparseable_named_file(self) -> None:
        root = self._tool_dir("def (:\n", None)
        code, _o, err = self._run(["--path", str(root / "tool.py")])
        self.assertEqual(code, 2)
        self.assertIn("cannot parse", err)

    def test_clean_tool_passes(self) -> None:
        root = self._tool_dir(self._CLEAN_PY, self._CLEAN_MD)
        with mock.patch.object(mod, "_render_help", return_value=None):
            code, out, _e = self._run(["--path", str(root / "tool.py")])
        self.assertEqual(code, 0)
        self.assertIn("FINAL RESULT: PASS", out)

    _DRIFTED_PY = """\
        import argparse
        def main():
            p = argparse.ArgumentParser()
            p.add_argument("--path", required=True, help="p")
            p.add_argument("--extra", help="undocumented")
            return 0
        """

    def test_drifted_tool_gates(self) -> None:
        root = self._tool_dir(self._DRIFTED_PY, self._CLEAN_MD)  # md omits --extra
        with mock.patch.object(mod, "_render_help", return_value=None):
            code, out, _e = self._run(["--path", str(root)])
        self.assertEqual(code, 1)
        self.assertIn("--extra", out)

    def test_json_output(self) -> None:
        root = self._tool_dir(self._CLEAN_PY, self._CLEAN_MD)
        with mock.patch.object(mod, "_render_help", return_value=None):
            code, out, _e = self._run(["--path", str(root / "tool.py"), "--json"])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(out)["passed"])

    def test_missing_doc_gates_by_default(self) -> None:
        # A discovered CLI shipping without its companion .md fails.
        root = self._tool_dir(self._CLEAN_PY, None)  # no tool.md written
        with mock.patch.object(mod, "_render_help", return_value=None):
            code, out, _e = self._run(["--path", str(root / "tool.py")])
        self.assertEqual(code, 1)
        self.assertIn("no companion doc", out)

    def test_missing_doc_allowed_passes(self) -> None:
        # --allow-missing-doc downgrades the missing-.md finding to advisory (exit 0).
        root = self._tool_dir(self._CLEAN_PY, None)  # no tool.md written
        with mock.patch.object(mod, "_render_help", return_value=None):
            code, out, _e = self._run(
                ["--path", str(root / "tool.py"), "--allow-missing-doc"]
            )
        self.assertEqual(code, 0)
        self.assertIn("FINAL RESULT: PASS", out)
