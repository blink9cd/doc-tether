"""Grouped suite: a file the tool cannot read is exit 2, never a finding or a crash.

Exit 1 means "ShellCheck reported findings", so an I/O fault that surfaced as 1 (or as a
traceback, which a shell also reads as non-zero) would send a reader to look for findings
that do not exist. Each case here pins one way reading or writing can fail, and the exit
and message it must produce instead.
"""

from __future__ import annotations

import errno
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

# The longest filename most filesystems take, in bytes (POSIX NAME_MAX).
_NAME_MAX = 255


def _no_findings(_exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
    return 0, ""


def _run(argv: list[str]) -> tuple[int, str, str]:
    """Run ``main`` with a backend that reports nothing; return (exit, out, err)."""
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.object(sut, "_run_shellcheck", _no_findings),
        mock.patch("sys.stdout", out),
        mock.patch("sys.stderr", err),
    ):
        code = sut.main(argv)
    return code, out.getvalue(), err.getvalue()


class _TreeCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()


class UnreadableMarkdownTests(_TreeCase):
    def test_undecodable_bytes_are_exit_2_naming_the_file(self) -> None:
        bad = self.root / "latin1.md"
        bad.write_bytes(b"# caf\xe9\n```bash\necho hi\n```\n")
        code, out, err = _run(["--files", str(bad)])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(str(bad), err)
        self.assertIn("cannot read", err)

    def test_every_unreadable_file_is_named_not_only_the_first(self) -> None:
        first, second = self.root / "a.md", self.root / "b.md"
        first.write_bytes(b"\xff\xfe\xfa")
        second.write_bytes(b"\xff\xfe\xfa")
        code, _out, err = _run(["--files", str(first), str(second)])
        self.assertEqual(code, 2)
        self.assertIn(str(first), err)
        self.assertIn(str(second), err)

    def test_an_os_error_on_read_is_unreadable_markdown(self) -> None:
        # A directory handed straight to the library raises an OSError on read
        # (IsADirectoryError on POSIX, PermissionError on Windows).
        folder = self.root / "folder.md"
        folder.mkdir()
        with self.assertRaises(sut.UnreadableMarkdown) as caught:
            sut.lint_markdown([folder])
        self.assertIn(str(folder), str(caught.exception))

    def test_a_byte_order_mark_does_not_hide_the_first_fence(self) -> None:
        md = self.root / "bom.md"
        md.write_bytes("﻿```bash\necho hi\n```\n".encode())
        seen: list[int] = []

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            seen.append(len(paths))
            return 0, ""

        with mock.patch.object(sut, "_run_shellcheck", fake):
            sut.lint_markdown([md])
        self.assertEqual(seen, [1])


class GlobMatchedDirectoryTests(_TreeCase):
    def test_a_directory_matching_the_glob_is_skipped(self) -> None:
        (self.root / "notes.md").mkdir()
        (self.root / "real.md").write_text("# x\n", encoding="utf-8", newline="\n")
        found = sut.discover_markdown_files(self.root, "**/*.md", [])
        self.assertEqual([p.name for p in found], ["real.md"])

    def test_a_root_run_over_such_a_directory_is_clean(self) -> None:
        (self.root / "notes.md").mkdir()
        code, out, _err = _run(["--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertIn("Checked 0 Markdown file(s)", out)


class ScriptNameTests(unittest.TestCase):
    @staticmethod
    def _block(stem: str) -> sut.ShellBlock:
        return sut.ShellBlock(Path(f"{stem}.md"), 3, 40, "bash", ("echo",))

    def test_a_long_stem_cannot_exceed_name_max(self) -> None:
        name = sut.script_name(9999, self._block("n" * 400))
        self.assertLessEqual(len(name.encode("utf-8")), _NAME_MAX)

    def test_a_non_ascii_stem_cannot_exceed_name_max(self) -> None:
        name = sut.script_name(1, self._block("é" * 200))
        self.assertLessEqual(len(name.encode("utf-8")), _NAME_MAX)

    def test_the_name_keeps_the_serial_block_and_line(self) -> None:
        name = sut.script_name(12, self._block("notes"))
        self.assertEqual(name, "0012_notes__blk3__L40.sh")

    def test_characters_unsafe_in_a_filename_are_replaced(self) -> None:
        name = sut.script_name(1, self._block("a b;c$d"))
        self.assertEqual(name, "0001_a_b_c_d__blk3__L40.sh")

    def test_a_long_stem_still_writes_and_lints(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            stem = "n" * (_NAME_MAX - len(".md"))
            md = Path(directory) / f"{stem}.md"
            try:
                md.write_text("```bash\necho hi\n```\n", encoding="utf-8", newline="\n")
            except OSError:  # pragma: no cover - a filesystem with a shorter limit
                self.skipTest("this filesystem refuses a NAME_MAX-long filename")
            with mock.patch.object(sut, "_run_shellcheck", _no_findings):
                self.assertEqual(sut.lint_markdown([md]), [])


class ScratchWriteTests(_TreeCase):
    """A fault writing the temporary scripts names the write, never the backend.

    It used to surface through the could-not-run branch, as "not found on PATH" for a
    ``FileNotFoundError``: a reader reinstalled a ShellCheck that was never the problem.
    """

    def _run_with(self, target: str, *, rc: bool = False) -> tuple[int, str]:
        md = self.root / "doc.md"
        md.write_text("```bash\necho hi\n```\n", encoding="utf-8", newline="\n")
        if rc:
            (self.root / ".shellcheckrc").write_text("", encoding="utf-8", newline="\n")
        fault = FileNotFoundError(errno.ENOENT, "No such file or directory")
        with mock.patch(target, side_effect=fault):
            code, _out, err = _run(["--files", str(md)])
        return code, err

    def _assert_names_the_scratch(self, code: int, err: str, words: str) -> None:
        self.assertEqual(code, 2)
        self.assertIn(words, err)
        self.assertNotIn("could not run shellcheck", err)
        self.assertNotIn("not found on PATH", err)

    def test_a_script_write_fault_is_its_own_exit_2(self) -> None:
        code, err = self._run_with("pathlib.Path.write_text")
        self._assert_names_the_scratch(
            code, err, "could not write the extracted script"
        )

    def test_an_rc_copy_fault_is_its_own_exit_2(self) -> None:
        code, err = self._run_with("shutil.copyfile", rc=True)
        self._assert_names_the_scratch(
            code, err, "could not copy the ShellCheck rc file"
        )

    def test_a_group_folder_fault_is_its_own_exit_2(self) -> None:
        code, err = self._run_with("pathlib.Path.mkdir")
        self._assert_names_the_scratch(
            code, err, "could not write the extracted scripts'"
        )

    def test_a_scratch_directory_fault_is_its_own_exit_2(self) -> None:
        code, err = self._run_with("tempfile.TemporaryDirectory")
        self._assert_names_the_scratch(
            code, err, "could not create a temporary directory"
        )


@unittest.skipIf(os.name == "nt", "chmod cannot make a file unreadable on Windows")
class PermissionDeniedTests(_TreeCase):
    def test_an_unreadable_file_is_exit_2(self) -> None:  # pragma: only-on posix
        md = self.root / "locked.md"
        md.write_text("```bash\necho hi\n```\n", encoding="utf-8", newline="\n")
        md.chmod(0)
        self.addCleanup(md.chmod, 0o600)
        if os.access(md, os.R_OK):  # pragma: no cover - running as root
            self.skipTest("this user can read a mode-0 file (root)")
        code, _out, err = _run(["--files", str(md)])
        self.assertEqual(code, 2)
        self.assertIn(str(md), err)


if __name__ == "__main__":
    unittest.main()
