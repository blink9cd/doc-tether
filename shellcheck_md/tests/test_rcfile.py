"""Grouped suite: a project's ShellCheck rc file reaches the scripts lifted from its docs.

ShellCheck looks for ``.shellcheckrc`` (or ``shellcheckrc``) beside the script it checks and
in each parent directory. The extracted scripts live in a temporary directory, so that
search never saw the project's rc file and its directives were silently ignored. The tool
now finds the rc file nearest each Markdown file and copies it, as ``.shellcheckrc``, into
the temporary subdirectory holding the scripts lifted from files that share it, so
ShellCheck's own search finds the copy on every version. ``--rcfile`` is not used: it arrived
only in ShellCheck 0.10.0. Scripts from a file with no rc file sit in a subdirectory with
none, so the user's own rc file still applies to them.
"""

from __future__ import annotations

import functools
import io
import os
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above

_BLOCK = "```bash\necho hi\n```\n"
_REAL_NEAREST = sut.nearest_rcfile


class _TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def _write(self, relative: str, body: str = "") -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
        return path


class NearestRcfileTests(_TreeCase):
    def test_the_dotted_name_beside_the_file_is_found(self) -> None:
        rc = self._write(".shellcheckrc")
        md = self._write("doc.md")
        self.assertEqual(sut.nearest_rcfile(md.parent, stop=self.root), rc)

    def test_the_undotted_name_is_found_too(self) -> None:
        rc = self._write("shellcheckrc")
        md = self._write("doc.md")
        self.assertEqual(sut.nearest_rcfile(md.parent, stop=self.root), rc)

    def test_the_dotted_name_wins_in_one_directory(self) -> None:
        rc = self._write(".shellcheckrc")
        self._write("shellcheckrc")
        self.assertEqual(sut.nearest_rcfile(self.root, stop=self.root), rc)

    def test_the_nearer_ancestor_wins(self) -> None:
        self._write(".shellcheckrc")
        inner = self._write("sub/.shellcheckrc")
        md = self._write("sub/deeper/doc.md")
        self.assertEqual(sut.nearest_rcfile(md.parent, stop=self.root), inner)

    def test_none_found_up_to_the_ceiling_is_none(self) -> None:
        md = self._write("sub/doc.md")
        self.assertIsNone(sut.nearest_rcfile(md.parent, stop=self.root))

    def test_a_directory_with_the_rc_name_is_not_an_rc_file(self) -> None:
        (self.root / ".shellcheckrc").mkdir()
        self.assertIsNone(sut.nearest_rcfile(self.root, stop=self.root))


class BuildArgsTests(unittest.TestCase):
    def test_the_argv_carries_no_rcfile_flag(self) -> None:
        args = sut.build_shellcheck_args("sc", [Path("a.sh")])
        self.assertEqual(args, ["sc", sut.GCC_FORMAT_FLAG, "a.sh"])


class RcfileReachesTheScriptsTests(_TreeCase):
    """Each call's scripts share one directory, holding a copy of their rc file or none."""

    def _calls(self, mds: Sequence[Path]) -> list[tuple[str | None, int]]:
        """Per call: the rc text beside its scripts (None when absent), and its count."""
        calls: list[tuple[str | None, int]] = []

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            folders = {Path(p).parent for p in paths}
            self.assertEqual(len(folders), 1, "one call's scripts share one folder")
            copy = folders.pop() / ".shellcheckrc"
            text = copy.read_text(encoding="utf-8") if copy.is_file() else None
            calls.append((text, len(paths)))
            return 0, ""

        with (
            mock.patch.object(sut, "_run_shellcheck", fake),
            # Bounded at the temp root, so a host rc file above it cannot answer.
            mock.patch.object(
                sut, "nearest_rcfile", functools.partial(_REAL_NEAREST, stop=self.root)
            ),
        ):
            sut.lint_markdown(mds)
        return calls

    def test_each_markdown_files_nearest_rc_is_copied_beside_its_scripts(self) -> None:
        self._write("a/.shellcheckrc", "disable=SC2034\n")
        self._write("b/shellcheckrc", "disable=SC2086\n")
        md_a = self._write("a/doc.md", _BLOCK)
        md_b = self._write("b/doc.md", _BLOCK * 2)
        calls = self._calls([md_a, md_b])
        self.assertEqual(
            sorted(calls, key=str),
            sorted([("disable=SC2034\n", 1), ("disable=SC2086\n", 2)], key=str),
        )

    def test_a_file_with_no_rc_gets_a_folder_with_none(self) -> None:
        md = self._write("doc.md", _BLOCK)
        self.assertEqual(self._calls([md]), [(None, 1)])

    def test_files_with_and_without_an_rc_never_share_a_folder(self) -> None:
        self._write("a/.shellcheckrc", "disable=SC2034\n")
        md_a = self._write("a/doc.md", _BLOCK)
        md_b = self._write("b/doc.md", _BLOCK)
        calls = self._calls([md_a, md_b])
        self.assertEqual(
            sorted(calls, key=str),
            sorted([("disable=SC2034\n", 1), (None, 1)], key=str),
        )


def _can_symlink(directory: Path) -> bool:
    """Whether a symlink can be created here (Windows needs a privilege for it)."""
    probe = directory / "symlink-probe"
    try:
        probe.symlink_to(directory / "absent")
    except (OSError, NotImplementedError):
        return False
    probe.unlink()
    return True


class SymlinkedRcfileTests(_TreeCase):
    """A symlinked rc file is refused, never followed into the temporary directory.

    Copying through the link would read whatever file it names, outside the project too,
    and hand it to ShellCheck. The refusal is exit 2 and names the link, so the run is
    neither silently clean nor silently missing the project's directives.
    """

    def setUp(self) -> None:
        super().setUp()
        if not _can_symlink(self.root):
            self.skipTest("symlinks cannot be created here")
        target = self._write("outside/secret.txt", "not an rc file\n")
        self.link = self.root / "proj" / ".shellcheckrc"
        self.link.parent.mkdir()
        self.link.symlink_to(target)
        self.md = self._write("proj/doc.md", _BLOCK)

    def _main(self) -> tuple[int, str]:
        def never(*_args: object) -> tuple[int, str]:
            raise AssertionError("shellcheck must not run on a refused rc file")

        err = io.StringIO()
        with (
            mock.patch.object(sut, "_run_shellcheck", never),
            mock.patch.object(
                sut, "nearest_rcfile", functools.partial(_REAL_NEAREST, stop=self.root)
            ),
            mock.patch("sys.stdout", io.StringIO()),
            mock.patch("sys.stderr", err),
        ):
            code = sut.main(["--files", str(self.md)])
        return code, err.getvalue()

    def test_a_symlinked_rc_file_is_exit_2_naming_it(self) -> None:
        code, err = self._main()
        self.assertEqual(code, 2)
        self.assertIn(f"refusing a symlinked ShellCheck rc file: {self.link}", err)

    def test_the_refusal_is_a_scratch_write_error(self) -> None:
        with (
            mock.patch.object(
                sut, "nearest_rcfile", functools.partial(_REAL_NEAREST, stop=self.root)
            ),
            self.assertRaises(sut.ScratchWriteError),
        ):
            sut.lint_markdown([self.md])

    @unittest.skipIf(os.name == "nt", "Windows refuses a control character in a name")
    def test_a_hostile_link_path_is_spelled_out(self) -> None:
        hostile = self.root / "p\x1b[31m"
        hostile.mkdir()
        link = hostile / "shellcheckrc"
        link.symlink_to(self.root / "outside" / "secret.txt")
        self.md = self._write("p\x1b[31m/doc.md", _BLOCK)
        code, err = self._main()
        self.assertEqual(code, 2)
        self.assertNotIn("\x1b", err)
        self.assertIn("p\\x1b[31m", err)


if __name__ == "__main__":
    unittest.main()
