"""Grouped suite: results from several rc-file groups read as one run.

The scripts are run in groups, one per rc file, so the order the backend reports in is the
order of the groups rather than of the files. These cases pin that the report is sorted by
file, line and column whatever the grouping, and that findings an earlier group produced
are still printed when a later group reaches no verdict.
"""

from __future__ import annotations

import functools
import io
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


def _finding_line(path: Path | str) -> str:
    return f"{path}:2:1: warning: Issue. [SC1234]\n"


class _GroupCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()

    def _write(self, relative: str, body: str = _BLOCK) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8", newline="\n")
        return path

    def _run(self, argv: list[str], seam: object) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sut, "_run_shellcheck", seam),
            mock.patch.object(
                sut, "nearest_rcfile", functools.partial(_REAL_NEAREST, stop=self.root)
            ),
            mock.patch("sys.stdout", out),
            mock.patch("sys.stderr", err),
        ):
            code = sut.main(argv)
        return code, out.getvalue(), err.getvalue()


class OrderTests(_GroupCase):
    def test_findings_print_in_file_order_whatever_the_grouping(self) -> None:
        # x and z share the rc file at the root while y has its own, so the groups run
        # x+z and then y: unsorted, the report would read a, c, b.
        self._write(".shellcheckrc", "# shared\n")
        self._write("y/.shellcheckrc", "# own\n")
        files = [self._write("x/a.md"), self._write("y/b.md"), self._write("z/c.md")]

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 1, "".join(_finding_line(p) for p in paths)

        code, out, _err = self._run(["--files", *map(str, files)], fake)
        self.assertEqual(code, 1)
        reported = [line.split(":2:1:")[0] for line in out.splitlines()[:3]]
        self.assertEqual(reported, sorted(map(str, files)))


class LaterGroupFailsTests(_GroupCase):
    def _two_groups(self) -> list[str]:
        self._write("a/.shellcheckrc", "# a\n")
        return [str(self._write("a/doc.md")), str(self._write("b/doc.md"))]

    def test_an_earlier_partial_group_is_printed_when_a_later_one_fails(self) -> None:
        calls: list[int] = []

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            calls.append(len(paths))
            if len(calls) == 1:
                return 2, _finding_line(paths[0]) + "doc.sh: could not read\n"
            return 3, "bad option\n"

        code, out, err = self._run(["--files", *self._two_groups()], fake)
        self.assertEqual(code, 2)
        self.assertIn(f"{self.root / 'a' / 'doc.md'}:2:1: warning: Issue.", out)
        self.assertIn("1 shellcheck finding(s)", out)
        self.assertIn("exited 3", err)
        self.assertIn("could not read", err)

    def test_an_earlier_complete_groups_findings_are_printed_too(self) -> None:
        calls: list[int] = []

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            calls.append(len(paths))
            if len(calls) == 1:
                return 1, _finding_line(paths[0])
            return 3, "bad option\n"

        code, out, err = self._run(["--files", *self._two_groups()], fake)
        self.assertEqual(code, 2)
        self.assertIn("1 shellcheck finding(s)", out)
        self.assertIn("exited 3", err)

    def test_a_first_group_failing_prints_no_report(self) -> None:
        def fake(_exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 3, "bad option\n"

        code, out, err = self._run(["--files", *self._two_groups()], fake)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("exited 3", err)


if __name__ == "__main__":
    unittest.main()
