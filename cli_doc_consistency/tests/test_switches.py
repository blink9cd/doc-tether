"""Grouped suite: the options that change WHAT runs -- ``--doc`` and the two opt-ins.

``--doc`` names the doc to check in place of the sibling ``<name>.md``. ``--run-help`` and
``--check-positionals`` each turn on one behavior that is off by default for a stranger's
tree: executing the target to read its live defaults, and the named-options policy. Each
opt-in has a flag and a ``[tool.cli_doc_consistency]`` key, and either one turns it on; a key
that is not a boolean stops the run, because a typo there must not silently switch a check
off.
"""

from __future__ import annotations

import contextlib
import io
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above

# cli_doc_consistency's import runs its sys.path bootstrap, so this resolves after it.
import pyproject_config  # noqa: E402 - same bootstrap
from _fixtures import parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir

_PY = """\
    import argparse
    def main():
        p = argparse.ArgumentParser()
        p.add_argument("--path", required=True, help="p")
        p.add_argument("--count", type=int, default=5, help="c")
        p.add_argument("target")
        return 0
    """
_TABLE_MD = (
    "| Option | Default |\n| --- | --- |\n| `--path P` | — |\n| `--count N` | 5 |\n"
)
_README = "# tool\n\n```text\nusage: tool.py --path P\n\noptions:\n  --path P  p\n  --count N  c\n```\n"


class ConfigSwitchesTest(unittest.TestCase):
    """``run_help`` / ``check_positionals`` read from the nearest pyproject."""

    _BASE = Path("/proj").resolve()

    def _read(self, body: str | None) -> mod.ConfigSwitches:
        return mod.read_config_switches(
            self._BASE / "sub",
            is_file=lambda p: body is not None and p == self._BASE / "pyproject.toml",
            read_text=lambda _p: body or "",
        )

    def test_absent_everything_is_off(self) -> None:
        self.assertEqual(self._read(None), mod.ConfigSwitches(False, False))
        self.assertEqual(self._read("[tool.other]\n"), mod.ConfigSwitches(False, False))

    def test_both_keys_on(self) -> None:
        body = "[tool.cli_doc_consistency]\nrun_help = true\ncheck_positionals = true\n"
        self.assertEqual(self._read(body), mod.ConfigSwitches(True, True))

    def test_one_key_explicitly_off(self) -> None:
        body = (
            "[tool.cli_doc_consistency]\nrun_help = false\ncheck_positionals = true\n"
        )
        self.assertEqual(self._read(body), mod.ConfigSwitches(False, True))

    def test_a_non_boolean_is_a_config_error(self) -> None:
        body = '[tool.cli_doc_consistency]\nrun_help = "yes"\n'
        with self.assertRaises(pyproject_config.ConfigError) as ctx:
            self._read(body)
        self.assertIn("run_help", str(ctx.exception))


class AnalyzeFileSwitchesTest(unittest.TestCase):
    def test_positionals_are_checked_only_when_asked(self) -> None:
        tree = parse_module(_PY)
        found = {
            flag: mod.analyze_file(
                Path("t.py"),
                Path("t.md"),
                tree,
                _TABLE_MD,
                render_help=lambda _p, _py: None,
                python="py",
                check_positionals=flag,
            )
            for flag in (False, True)
        }
        self.assertFalse(any(f.check == mod.CHECK_D for f in found[False]))
        (finding,) = [f for f in found[True] if f.check == mod.CHECK_D]
        self.assertEqual(
            finding.message,
            "'target' is a positional CLI argument; user-facing arguments should be "
            "named options, which are self-documenting and order-independent",
        )


class MainSwitchesTest(unittest.TestCase):
    def _run(self, args: list[str]) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = mod.main(args)
        return code, out.getvalue(), err.getvalue()

    def _tool_dir(self, config: str = "[tool.other]\n") -> Path:
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name)
        (root / "pyproject.toml").write_text(config, encoding="utf-8", newline="\n")
        (root / "tool.py").write_text(
            textwrap.dedent(_PY), encoding="utf-8", newline="\n"
        )
        (root / "tool.md").write_text(_TABLE_MD, encoding="utf-8", newline="\n")
        (root / "README.md").write_text(_README, encoding="utf-8", newline="\n")
        return root

    def test_parser_accepts_the_new_options(self) -> None:
        args = mod.build_parser().parse_args(
            ["--path", "x", "--doc", "d", "--run-help", "--check-positionals"]
        )
        self.assertEqual(args.doc, "d")
        self.assertTrue(args.run_help)
        self.assertTrue(args.check_positionals)
        plain = mod.build_parser().parse_args(["--path", "x"])
        self.assertIsNone(plain.doc)
        self.assertFalse(plain.run_help)
        self.assertFalse(plain.check_positionals)
        self.assertFalse(plain.no_run_help)
        self.assertTrue(
            mod.build_parser().parse_args(["--path", "x", "--no-run-help"]).no_run_help
        )

    def test_doc_names_another_doc(self) -> None:
        # The sibling tool.md drifts (it omits --count) while README.md is complete, so
        # the two docs give different verdicts: a --doc that was ignored would FAIL.
        root = self._tool_dir()
        (root / "tool.md").write_text(
            "| Option |\n| --- |\n| `--path P` |\n", encoding="utf-8", newline="\n"
        )
        tool = str(root / "tool.py")
        with mock.patch.object(mod, "_render_help") as render:
            code, out, _e = self._run(
                ["--path", tool, "--doc", str(root / "README.md")]
            )
            sibling_code, sibling_out, _e = self._run(["--path", tool])
        render.assert_not_called()
        self.assertEqual(code, 0, out)
        self.assertIn("FINAL RESULT: PASS", out)
        self.assertEqual(sibling_code, 1, sibling_out)
        self.assertIn("--count", sibling_out)

    def test_no_run_help_beats_the_config(self) -> None:
        # The scanned tree's own pyproject asks for execution; --no-run-help refuses.
        root = self._tool_dir("[tool.cli_doc_consistency]\nrun_help = true\n")
        with mock.patch.object(mod, "_render_help") as render:
            code, out, _e = self._run(["--path", str(root), "--no-run-help"])
        render.assert_not_called()
        self.assertEqual(code, 0, out)
        self.assertIn("--run-help", out)  # the defaults-not-checked advisory

    def test_run_help_and_no_run_help_are_exclusive(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as ctx:
            mod.build_parser().parse_args(
                ["--path", "x", "--run-help", "--no-run-help"]
            )
        self.assertEqual(ctx.exception.code, 2)
        self.assertIn("not allowed with", err.getvalue())

    def test_doc_with_a_directory_path_is_a_usage_error(self) -> None:
        root = self._tool_dir()
        code, _o, err = self._run(["--path", str(root), "--doc", str(root / "tool.md")])
        self.assertEqual(code, 2)
        self.assertIn("--doc", err)

    def test_a_missing_doc_is_a_usage_error(self) -> None:
        root = self._tool_dir()
        code, _o, err = self._run(
            ["--path", str(root / "tool.py"), "--doc", str(root / "nope.md")]
        )
        self.assertEqual(code, 2)
        self.assertIn("--doc", err)

    def test_defaults_are_skipped_without_run_help(self) -> None:
        root = self._tool_dir()
        with mock.patch.object(mod, "_render_help") as render:
            code, out, _e = self._run(["--path", str(root / "tool.py")])
        render.assert_not_called()
        self.assertEqual(code, 0, out)
        self.assertIn("--run-help", out)

    def test_run_help_flag_renders(self) -> None:
        root = self._tool_dir()
        with mock.patch.object(mod, "_render_help", return_value=None) as render:
            code, _o, _e = self._run(["--path", str(root / "tool.py"), "--run-help"])
        render.assert_called_once()
        self.assertEqual(code, 0)

    def test_run_help_config_renders(self) -> None:
        root = self._tool_dir("[tool.cli_doc_consistency]\nrun_help = true\n")
        with mock.patch.object(mod, "_render_help", return_value=None) as render:
            code, _o, _e = self._run(["--path", str(root / "tool.py")])
        render.assert_called_once()
        self.assertEqual(code, 0)

    def test_check_positionals_flag_gates(self) -> None:
        root = self._tool_dir()
        code, out, _e = self._run(
            ["--path", str(root / "tool.py"), "--check-positionals"]
        )
        self.assertEqual(code, 1)
        self.assertIn("[D gating]", out)

    def test_check_positionals_config_gates(self) -> None:
        root = self._tool_dir("[tool.cli_doc_consistency]\ncheck_positionals = true\n")
        code, out, _e = self._run(["--path", str(root)])
        self.assertEqual(code, 1)
        self.assertIn("[D gating]", out)

    def test_a_non_boolean_switch_stops_the_run(self) -> None:
        root = self._tool_dir("[tool.cli_doc_consistency]\ncheck_positionals = 1\n")
        code, _o, err = self._run(["--path", str(root)])
        self.assertEqual(code, 2)
        self.assertIn("check_positionals", err)


if __name__ == "__main__":
    unittest.main()
