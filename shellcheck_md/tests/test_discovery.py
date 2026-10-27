"""Grouped suite: which Markdown files this gate examines.

Globbing, the default directory prune, the PEP 405 virtualenv test, --exclude, and the
[tool.shellcheck_md] standing excludes -- one module because they all answer the same
question, and a defect in any of them changes the examined set rather than the verdict
logic.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above


class ShouldExcludeTests(unittest.TestCase):
    def test_equal_parent_and_miss(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "sub" / "f.md"
            target.parent.mkdir(parents=True)
            target.write_text("x", encoding="utf-8", newline="")
            self.assertTrue(sut.should_exclude(target, [target.resolve()]))
            self.assertTrue(sut.should_exclude(target, [(root / "sub").resolve()]))
            self.assertFalse(sut.should_exclude(target, [(root / "other").resolve()]))
            self.assertFalse(sut.should_exclude(target, []))


class DiscoverMarkdownFilesTests(unittest.TestCase):
    def test_globs_and_prunes_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "a.md").write_text("a\n", encoding="utf-8", newline="")
            (root / "sub").mkdir()
            (root / "sub" / "b.md").write_text("b\n", encoding="utf-8", newline="")
            skip = root / "skip"
            skip.mkdir()
            (skip / "c.md").write_text("c\n", encoding="utf-8", newline="")
            found = sut.discover_markdown_files(root, "**/*.md", [skip.resolve()])
            self.assertEqual([p.name for p in found], ["a.md", "b.md"])

    def test_a_root_inside_a_pruned_name_still_discovers(self) -> None:
        """The prune judges the parts BELOW --root, never the root's own path."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "node_modules" / "pkg"
            root.mkdir(parents=True)
            (root / "README.md").write_text("r\n", encoding="utf-8", newline="")
            (root / ".git").mkdir()
            (root / ".git" / "x.md").write_text("g\n", encoding="utf-8", newline="")
            found = sut.discover_markdown_files(root, "**/*.md", [])
            self.assertEqual([p.name for p in found], ["README.md"])

    def test_prunes_default_dirs(self) -> None:
        # A node project's dependency READMEs (under node_modules) must not be
        # lifted for shellcheck -- they are not ours to lint.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "keep.md").write_text("k\n", encoding="utf-8", newline="")
            dep = root / "node_modules" / "acorn"
            dep.mkdir(parents=True)
            (dep / "README.md").write_text(
                "```bash\nbad {\n```\n", encoding="utf-8", newline=""
            )
            found = sut.discover_markdown_files(root, "**/*.md", [])
            self.assertEqual([p.name for p in found], ["keep.md"])


def _finding_per_script(
    _exe: str, paths: Sequence[Path], _timeout: float
) -> tuple[int, str]:
    """Stand in for shellcheck: one finding on every script it is handed.

    Never the real backend, so these cases cannot reach the host -- and an exclude that
    stops binding surfaces as exit 1 rather than depending on what a real shellcheck would
    have found in the fixture.
    """
    return 1, "".join(f"{p}:2:1: warning: Issue. [SC2034]\n" for p in paths)


def _run_main(argv: list[str]) -> tuple[int, str, str]:
    """Run shellcheck_md.main with the faked backend, capturing (exit, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(sut, "_run_shellcheck", _finding_per_script),
        mock.patch("sys.stdout", out),
        mock.patch("sys.stderr", err),
    ):
        code = sut.main(["--root", str(argv[0]), *argv[1:]])
    return code, out.getvalue(), err.getvalue()


class TestConfigExcludes(unittest.TestCase):
    """[tool.shellcheck_md] standing excludes: read, resolved, unioned, and fail-loud."""

    def test_no_pyproject_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            self.assertEqual(sut.read_config_excludes(base, stop=base), ())

    def test_table_present_resolved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["vendor", "scratch"]\n',
                encoding="utf-8",
                newline="",
            )
            self.assertEqual(
                sut.read_config_excludes(base),
                ((base / "vendor").resolve(), (base / "scratch").resolve()),
            )

    def test_no_table_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                "[tool.other]\n", encoding="utf-8", newline=""
            )
            self.assertEqual(sut.read_config_excludes(base, stop=base), ())

    def test_malformed_toml_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                "[tool.shellcheck_md\n", encoding="utf-8", newline=""
            )
            with self.assertRaises(sut.ConfigError):
                sut.read_config_excludes(base, stop=base)

    def test_non_list_exclude_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = "vendor"\n',
                encoding="utf-8",
                newline="",
            )
            with self.assertRaises(sut.ConfigError):
                sut.read_config_excludes(base, stop=base)

    def test_non_string_entry_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                "[tool.shellcheck_md]\nexclude = [1]\n", encoding="utf-8", newline=""
            )
            with self.assertRaises(sut.ConfigError):
                sut.read_config_excludes(base, stop=base)

    def test_stop_ceiling_hides_higher_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["vendor"]\n',
                encoding="utf-8",
                newline="",
            )
            nested = base / "inner"
            nested.mkdir()
            self.assertEqual(sut.read_config_excludes(nested, stop=nested), ())

    def test_main_applies_config_exclude(self) -> None:
        # A discoverable .md file under an excluded dir must be skipped (exit 0).
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["vendor"]\n',
                encoding="utf-8",
                newline="",
            )
            vendor = base / "vendor"
            vendor.mkdir()
            # A fenced bash block that shellcheck would flag if it were processed.
            (vendor / "x.md").write_text(
                "```bash\nx=1\necho $x\n```\n", encoding="utf-8", newline=""
            )
            code, _out, _err = _run_main([str(base)])
            self.assertEqual(code, 0)

    def test_main_applies_each_files_nearest_config(self) -> None:
        """--root means what --files means: each file answers to its NEAREST pyproject.

        Before, --root read only the pyproject nearest the root, so a subproject's own
        standing excludes bound a pre-commit run but not a --root run of the same tree.
        """
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            sub = base / "sub"
            (sub / "vendor").mkdir(parents=True)
            (sub / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["vendor"]\n',
                encoding="utf-8",
                newline="",
            )
            (sub / "vendor" / "x.md").write_text(
                "```bash\nx=1\n```\n", encoding="utf-8", newline=""
            )
            code, out, _err = _run_main([str(base)])
            self.assertEqual(code, 0)
            self.assertIn("NOTHING EXAMINED", out)

    def test_main_malformed_config_is_exit_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                "[tool.shellcheck_md\n", encoding="utf-8", newline=""
            )
            code, _out, err = _run_main([str(base)])
            self.assertEqual(code, 2)
            self.assertIn("ERROR:", err)

    def test_a_nearer_pyproject_without_the_table_is_passed_over(self) -> None:
        """As ruff does: a sub-project's unrelated pyproject cannot cancel the root's."""
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["sub/vendor"]\n',
                encoding="utf-8",
                newline="",
            )
            sub = base / "sub"
            sub.mkdir()
            (sub / "pyproject.toml").write_text(
                '[project]\nname = "sub"\n', encoding="utf-8", newline=""
            )
            self.assertEqual(
                sut.read_config_excludes(sub, stop=base),
                ((base / "sub" / "vendor").resolve(),),
            )

    def test_a_nearer_table_without_exclude_still_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["vendor"]\n',
                encoding="utf-8",
                newline="",
            )
            sub = base / "sub"
            sub.mkdir()
            (sub / "pyproject.toml").write_text(
                "[tool.shellcheck_md]\n", encoding="utf-8", newline=""
            )
            self.assertEqual(sut.read_config_excludes(sub, stop=base), ())

    def test_a_table_that_is_not_a_table_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                "[tool]\nshellcheck_md = 3\n", encoding="utf-8", newline=""
            )
            with self.assertRaises(sut.ConfigError):
                sut.read_config_excludes(base, stop=base)

    def test_non_table_tool(self) -> None:
        # Exercises the ``tool is not dict`` branch of read_config_excludes.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                "tool = 3\n", encoding="utf-8", newline=""
            )
            self.assertEqual(sut.read_config_excludes(base, stop=base), ())

    def test_stop_not_ancestor_does_not_truncate(self) -> None:
        # stop is not in chain -> ceiling branch False arm; walk is untruncated.
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            (base / "pyproject.toml").write_text(
                '[tool.shellcheck_md]\nexclude = ["vendor"]\n',
                encoding="utf-8",
                newline="",
            )
            other = base / "sibling"
            other.mkdir()
            result = sut.read_config_excludes(base, stop=other)
            self.assertEqual(result, ((base / "vendor").resolve(),))


class VenvPruneTest(unittest.TestCase):
    """A virtualenv is skipped by its PEP 405 marker, not by its name.

    This gate globs and then filters, so it has no directory-prune step; the check is
    the ancestor form. Named ".tool-env" deliberately, so _DEFAULT_EXCLUDE_DIRS
    cannot make it pass on its own.
    """

    def test_markdown_inside_a_virtualenv_is_not_discovered(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "keep.md").write_text("# keep\n", encoding="utf-8", newline="")
            packages = root / ".tool-env" / "site-packages"
            packages.mkdir(parents=True)
            marker = root / ".tool-env" / "pyvenv.cfg"
            marker.write_text("", encoding="utf-8", newline="")
            (packages / "README.md").write_text(
                "# vendored\n", encoding="utf-8", newline=""
            )
            found = sut.discover_markdown_files(root, "**/*.md", [])
        self.assertEqual([p.name for p in found], ["keep.md"])
