"""Grouped suite: the argv ceiling, splitting the batch and naming an overflow correctly.

Two failures that look like one. A tree large enough to put every extracted script on a
single command line overflows the OS ceiling, and Windows reports that as ``WinError 206``,
which CPython raises as ``FileNotFoundError``: the same class a genuinely absent executable
raises, carrying the same ``errno``. So this module pins both halves of the defense: the
split that stops the overflow happening, and the diagnostic that tells the two apart if one
ever does.

The reproduction that prompted it: 528 discovered Markdown files lifted into one shellcheck
argv, refused by the OS, and reported as "could not run shellcheck executable 'shellcheck'
(not found on PATH or at that path)", naming a backend that was present and runnable.

Every count below is derived, never a literal. The extracted scripts live in a temp
directory whose width belongs to the platform (a POSIX ``/tmp/tmpXXXXXXXX`` entry
renders about thirty characters shorter than its Windows equivalent), so a block count
sized against one host stops overflowing on a shorter one, and these tests keep passing
over a single batch, proving nothing. That is not hypothetical: this module shipped a
hardcoded 600 and was green on Windows while asserting nothing on Linux or macOS.
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
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "argv_budget"))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above
import argv_budget  # noqa: E402 - reached by its own insert above


class ChunkScriptPathsTests(unittest.TestCase):
    """The split itself, against an explicit small limit so the arithmetic stays readable."""

    @staticmethod
    def _paths(count: int, width: int) -> list[Path]:
        # Zero-padded so every rendered token is exactly `width` characters, which makes the
        # expectations below arithmetic rather than approximation.
        return [Path(str(i).zfill(width)) for i in range(count)]

    def test_a_fitting_list_is_one_batch(self) -> None:
        batches = sut.chunk_script_paths(
            self._paths(4, 10), shellcheck="sc", limit=500, headroom=0
        )
        self.assertEqual(len(batches), 1)

    def test_an_overlong_list_is_split(self) -> None:
        batches = sut.chunk_script_paths(
            self._paths(40, 10), shellcheck="sc", limit=200, headroom=0
        )
        self.assertGreater(len(batches), 1)
        # Nothing may be lost in the split: a gate that silently skips a file reports a
        # clean run it never earned.
        self.assertEqual(sum(len(b) for b in batches), 40)

    def test_every_batch_fits_the_limit(self) -> None:
        limit = 200
        batches = sut.chunk_script_paths(
            self._paths(40, 10), shellcheck="sc", limit=limit, headroom=0
        )
        for batch in batches:
            argv = sut.build_shellcheck_args("sc", batch)
            self.assertLessEqual(argv_budget.rendered_length(argv), limit)

    def test_a_single_oversized_path_still_gets_a_batch(self) -> None:
        # argv_budget never drops a path it cannot fit; it isolates it, so an argv that
        # genuinely cannot be built surfaces as the backend's own failure rather than as a
        # silently smaller run.
        batches = sut.chunk_script_paths(
            self._paths(1, 400), shellcheck="sc", limit=100, headroom=0
        )
        self.assertEqual([len(b) for b in batches], [1])

    def test_the_prefix_is_charged_to_every_batch(self) -> None:
        # A long executable path shrinks each batch, because the argv head is re-paid per
        # invocation. Measuring that head rather than assuming it is what keeps the budget
        # honest when --shellcheck carries a deep path.
        paths = self._paths(40, 10)
        deep = sut.chunk_script_paths(
            paths, shellcheck="x" * 150, limit=300, headroom=0
        )
        bare = sut.chunk_script_paths(paths, shellcheck="sc", limit=300, headroom=0)
        self.assertGreater(len(deep), len(bare))

    def test_headroom_is_held_back_out_of_the_limit(self) -> None:
        paths = self._paths(40, 10)
        reserved = sut.chunk_script_paths(
            paths, shellcheck="sc", limit=300, headroom=200
        )
        spent = sut.chunk_script_paths(paths, shellcheck="sc", limit=300, headroom=0)
        self.assertGreater(len(reserved), len(spent))


#: Filename of the generated Markdown fixture. The writer and the measurement share it
#: because ``_script_name`` embeds the source's stem in every script path. Sizing a batch
#: against one name while writing another measures a command line that is never sent.
_MARKDOWN_NAME = "many.md"

#: Where the doubling search below starts. It decides only how many measurements that
#: search takes, never its answer: a Windows-width temp path needs roughly this many
#: blocks and a POSIX one about twice as many, so both settle within a couple of steps.
_SEED_BLOCKS = 256


def blocks_markdown(count: int) -> str:
    """A Markdown body holding *count* single-line ``bash`` fences."""
    return "".join(f"```bash\necho {i}\n```\n" for i in range(count))


def script_paths_for(script_dir: Path, count: int) -> list[Path]:
    """The exact temp-script paths ``lint_markdown`` builds for *count* fenced blocks.

    Mirrors its own loop through the tool's own extraction and naming rather than
    restating the filename shape here, so a change to either moves this measurement with
    it instead of leaving it quietly sized against the shape that used to hold.
    """
    blocks = sut.extract_blocks(
        blocks_markdown(count), Path(_MARKDOWN_NAME), sut.DEFAULT_LANGS
    )
    return [
        script_dir / sut.script_name(serial, block)
        for serial, block in enumerate(blocks)
    ]


def batches_for(script_dir: Path, count: int) -> list[list[Path]]:
    """How production would split *count* blocks' scripts written under *script_dir*.

    The tool's own chunker is the oracle. Asking it how many batches it will make, at
    the same ceiling and with the same default backend the run itself uses, is what keeps
    the search honest when the ceiling, the headroom or the argv head changes.
    """
    return sut.chunk_script_paths(
        script_paths_for(script_dir, count),
        shellcheck=sut.DEFAULT_SHELLCHECK,
        limit=argv_budget.argv_limit_for(sut.DEFAULT_SHELLCHECK),
    )


def over_ceiling_block_count(script_dir: Path) -> int:
    """Fenced-block count whose extracted scripts cannot fit one shellcheck argv.

    *script_dir* stands in for the temp directory ``lint_markdown`` opens for those
    scripts: ``TemporaryDirectory`` names every one of them off the same base with the
    same eight-character suffix, so a directory the caller already holds renders to the
    width the real run will pay, exactly.

    Derived rather than hardcoded because that width is the platform's, not ours (see
    the module docstring for what a literal did here). Growing by doubling: overshooting
    costs a few more temp files, undershooting costs the whole point of the test.
    """
    count = _SEED_BLOCKS
    while len(batches_for(script_dir, count)) < 2:
        count *= 2
    return count


class LintMarkdownBatchingTests(unittest.TestCase):
    """lint_markdown must split a real over-ceiling subject set, and merge every batch."""

    @staticmethod
    def _write_blocks(directory: Path) -> tuple[Path, int]:
        """Write a Markdown fixture into *directory* too wide for one argv; say how wide.

        The block count is measured against *directory* itself, which stands in for the
        one ``lint_markdown`` will open for the extracted scripts.
        """
        count = over_ceiling_block_count(directory)
        md = directory / _MARKDOWN_NAME
        md.write_text(blocks_markdown(count), encoding="utf-8", newline="\n")
        return md, count

    def test_a_subject_set_past_the_ceiling_is_split(self) -> None:
        calls: list[list[Path]] = []

        def fake(exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            calls.append(list(paths))
            argv = sut.build_shellcheck_args(exe, paths)
            # The point of the split: no single invocation may exceed what the OS accepts.
            self.assertLessEqual(
                argv_budget.rendered_length(argv), argv_budget.argv_limit_for(exe)
            )
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            md, count = self._write_blocks(Path(directory))
            with mock.patch.object(sut, "_run_shellcheck", fake):
                sut.lint_markdown([md])
        self.assertGreater(len(calls), 1, "the whole argv was passed in one call")
        self.assertEqual(sum(len(c) for c in calls), count)  # nothing dropped

    def test_findings_from_every_batch_are_merged(self) -> None:
        # A finding reported by each batch must survive: accumulating per batch is what
        # stops a later invocation's output being dropped on the floor.
        def fake(_exe: str, paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            return 1, f"{paths[-1]}:2:1: warning: Issue. [SC1234]\n"

        with tempfile.TemporaryDirectory() as directory:
            md, _count = self._write_blocks(Path(directory))
            with mock.patch.object(sut, "_run_shellcheck", fake):
                findings = sut.lint_markdown([md])
        self.assertGreater(len(findings), 1, "only one batch's findings came back")
        self.assertTrue(all(f.code == "SC1234" for f in findings))

    def test_the_bound_is_per_batch_not_per_run(self) -> None:
        # Each invocation carries the full --timeout-seconds, so a split list can take up
        # to batch-count times that bound. Pinned because the help text and the companion
        # doc both say so, and a shared-deadline rewrite would silently falsify them.
        seen: list[float] = []

        def fake(_exe: str, _paths: Sequence[Path], timeout: float) -> tuple[int, str]:
            seen.append(timeout)
            return 0, ""

        with tempfile.TemporaryDirectory() as directory:
            md, _count = self._write_blocks(Path(directory))
            with mock.patch.object(sut, "_run_shellcheck", fake):
                sut.lint_markdown([md], timeout=7.5)
        self.assertGreater(len(seen), 1)
        self.assertEqual(set(seen), {7.5})


class CouldNotRunDiagnosticTests(unittest.TestCase):
    """An over-long command line and an absent executable must not read the same.

    Both arrive as ``FileNotFoundError`` with ``errno == 2``; only ``winerror`` separates
    them (206 = ERROR_FILENAME_EXCED_RANGE against 2 = ERROR_FILE_NOT_FOUND). Reporting the
    overflow as "not found on PATH" sends the reader after an install problem that does not
    exist, which is the expensive direction to be wrong in.
    """

    @staticmethod
    def _main_stderr(raised: FileNotFoundError) -> tuple[int, str]:
        """Run main() over one fenced block whose shellcheck call raises *raised*."""

        def fake(_exe: str, _paths: Sequence[Path], _timeout: float) -> tuple[int, str]:
            raise raised

        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "x.md").write_text(
                "```bash\necho hi\n```\n", encoding="utf-8", newline="\n"
            )
            with (
                mock.patch.object(sut, "_run_shellcheck", fake),
                mock.patch("sys.stdout", io.StringIO()),
                mock.patch("sys.stderr", io.StringIO()) as err,
            ):
                code = sut.main(["--root", directory])
        return code, err.getvalue()

    @staticmethod
    def _overflow_error() -> FileNotFoundError:
        """A FileNotFoundError shaped exactly as CreateProcess's argv refusal.

        ``winerror`` is assigned explicitly rather than through the four-argument OSError
        constructor: that form populates the attribute only on Windows, so this test would
        silently assert the absent-executable branch on any other platform.

        The assignment goes through ``setattr`` for the same reason the production code
        reads it through ``getattr``: typeshed declares ``OSError.winerror`` only under
        sys.platform == "win32", so a plain ``exc.winerror = ...`` is an attr-defined error
        everywhere else. A
        ``# type: ignore`` would be the wrong repair: the attribute genuinely exists on
        Windows, so the ignore is unused there and inverts into a failure the moment
        warn_unused_ignores is turned on. setattr is correct on all three platforms at once.
        The runtime is unaffected: BaseException carries a __dict__, so the attribute is set
        and read back identically off Windows.
        """
        exc = FileNotFoundError(2, "The filename or extension is too long")
        setattr(exc, "winerror", sut.WINERROR_COMMAND_LINE_TOO_LONG)
        return exc

    def test_an_absent_executable_still_says_not_found(self) -> None:
        code, err = self._main_stderr(FileNotFoundError("shellcheck"))
        self.assertEqual(code, 2)
        self.assertIn("not found on PATH", err)

    def test_an_overlong_command_line_is_not_reported_as_missing(self) -> None:
        code, err = self._main_stderr(self._overflow_error())
        self.assertEqual(code, 2)  # same could-not-run channel, different cause
        self.assertNotIn("not found on PATH", err)

    def test_an_overlong_command_line_names_the_real_cause(self) -> None:
        _code, err = self._main_stderr(self._overflow_error())
        self.assertIn("command line", err)
        self.assertIn(str(sut.WINERROR_COMMAND_LINE_TOO_LONG), err)

    def test_the_two_diagnostics_differ(self) -> None:
        # The regression in one assertion: before these branches were told apart, both
        # printed the identical sentence.
        _absent_code, absent = self._main_stderr(FileNotFoundError("shellcheck"))
        _overflow_code, overflow = self._main_stderr(self._overflow_error())
        self.assertNotEqual(absent, overflow)


if __name__ == "__main__":
    unittest.main()
