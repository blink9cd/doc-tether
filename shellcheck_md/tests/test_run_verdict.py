"""Grouped suite: what ShellCheck's exit status means for the verdict.

ShellCheck exits 0 when it found nothing, 1 when it reported findings, 2 when some files
could not be processed, and 3 or 4 for bad options or an unknown output format. Reading only
its output and never its status lost all of that: an environment that changed the output
format (``SHELLCHECK_OPTS='-f json'``) turned two real findings into "0 finding(s)" and exit
0. Each case here pairs a status with an output and pins the exit the run must take.
"""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from collections.abc import Callable, Sequence
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above

_Seam = Callable[[str, Sequence[Path], float], tuple[int, str]]

# One bash block; the fakes below report on line 2 of the script, its only body line.
_DIRTY = "```bash\necho $x\n```\n"


def _replying(returncode: int, finding: bool, extra: str = "") -> _Seam:
    """A seam stand-in exiting *returncode*, with one mapped finding when *finding*."""

    def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
        text = f"{paths[0]}:2:6: note: Quote it. [SC2086]\n" if finding else ""
        return returncode, text + extra

    return fake


def _run_main(seam: _Seam, body: str = _DIRTY) -> tuple[int, str, str]:
    """Run ``main`` over one Markdown file with *seam* faked; return (exit, out, err)."""
    with tempfile.TemporaryDirectory() as directory:
        (Path(directory) / "x.md").write_text(body, encoding="utf-8", newline="\n")
        out, err = io.StringIO(), io.StringIO()
        with (
            mock.patch.object(sut, "_run_shellcheck", seam),
            mock.patch("sys.stdout", out),
            mock.patch("sys.stderr", err),
        ):
            code = sut.main(["--root", directory])
    return code, out.getvalue(), err.getvalue()


class ExitStatusContractTests(unittest.TestCase):
    def test_status_0_and_no_output_is_clean(self) -> None:
        code, out, _err = _run_main(_replying(0, finding=False))
        self.assertEqual(code, 0)
        self.assertIn("0 shellcheck finding(s)", out)

    def test_status_1_with_a_mapped_finding_is_exit_1(self) -> None:
        code, out, _err = _run_main(_replying(1, finding=True))
        self.assertEqual(code, 1)
        self.assertIn("x.md:2:6: note: Quote it. [SC2086]", out)

    def test_status_1_with_no_parseable_finding_is_could_not_run(self) -> None:
        """The reproduced defect: JSON output parsed as nothing and read as clean."""
        json_like = '[{"file":"x.sh","line":2,"code":2086}]\n'
        code, out, err = _run_main(_replying(1, finding=False, extra=json_like))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("could-not-run", err)
        self.assertIn("SHELLCHECK_OPTS", err)
        self.assertIn(json_like.strip(), err)  # the output tail, for diagnosis

    def test_status_1_with_only_unmapped_findings_is_could_not_run(self) -> None:
        stray = "/elsewhere/other.sh:3:1: warning: Stray. [SC2034]\n"
        code, _out, err = _run_main(_replying(1, finding=False, extra=stray))
        self.assertEqual(code, 2)
        self.assertIn("other.sh", err)

    def test_status_0_with_a_finding_is_an_anomaly_not_a_verdict(self) -> None:
        code, out, err = _run_main(_replying(0, finding=True))
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("exited 0", err)

    def test_status_2_prints_its_findings_and_exits_2(self) -> None:
        code, out, err = _run_main(
            _replying(2, finding=True, extra="x.sh: openBinaryFile: denied\n")
        )
        self.assertEqual(code, 2)
        self.assertIn("x.md:2:6: note: Quote it. [SC2086]", out)
        self.assertIn("exited 2", err)
        self.assertIn("openBinaryFile", err)

    def test_status_3_is_could_not_run_with_the_output_tail(self) -> None:
        code, out, err = _run_main(
            _replying(3, finding=False, extra="unrecognized option `--bogus'\n")
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("exited 3", err)
        self.assertIn("unrecognized option", err)

    def test_status_4_is_could_not_run(self) -> None:
        code, _out, err = _run_main(_replying(4, finding=False, extra="bad format\n"))
        self.assertEqual(code, 2)
        self.assertIn("exited 4", err)

    def test_an_unknown_status_is_could_not_run(self) -> None:
        code, _out, err = _run_main(_replying(7, finding=True))
        self.assertEqual(code, 2)
        self.assertIn("exited 7", err)

    def test_a_signal_death_is_could_not_run(self) -> None:
        code, _out, err = _run_main(_replying(-9, finding=False))
        self.assertEqual(code, 2)
        self.assertIn("signal 9", err)

    def test_a_status_2_batch_does_not_stop_the_later_ones(self) -> None:
        """Status 2 is partial, not fatal: every batch still runs and reports."""
        calls: list[int] = []

        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            calls.append(len(paths))
            return 2, f"{paths[0]}:2:6: note: Quote it. [SC2086]\n"

        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(sut, "_run_shellcheck", fake),
            mock.patch.object(
                sut,
                "chunk_script_paths",
                lambda paths, **_kw: [[p] for p in paths],
            ),
        ):
            md = Path(directory) / "x.md"
            md.write_text(_DIRTY * 2, encoding="utf-8", newline="\n")
            with self.assertRaises(sut.IncompleteRun) as caught:
                sut.lint_markdown([md])
        self.assertEqual(calls, [1, 1])
        self.assertEqual(len(caught.exception.findings), 2)


class OutputTailTests(unittest.TestCase):
    def test_a_long_output_keeps_only_its_last_lines(self) -> None:
        output = "".join(f"line {i}\n" for i in range(100))
        tail = sut.output_tail(output)
        self.assertIn("line 99", tail)
        self.assertNotIn("line 0\n", tail)
        self.assertEqual(len(tail.splitlines()), sut.OUTPUT_TAIL_LINES)

    def test_an_empty_output_says_so(self) -> None:
        self.assertEqual(sut.output_tail(""), "(no output)")


class SeamOSErrorTests(unittest.TestCase):
    def test_a_permission_error_starting_shellcheck_is_could_not_run(self) -> None:
        def denied(
            _exe: str, _paths: Sequence[Path], _timeout: float
        ) -> tuple[int, str]:
            raise PermissionError(13, "Permission denied")

        code, out, err = _run_main(denied)
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("could not run shellcheck", err)
        self.assertIn("Permission denied", err)


if __name__ == "__main__":
    unittest.main()
