#!/usr/bin/env python3
"""What one shellcheck invocation looks like, and what its result means.

Private to ``shellcheck_md`` -- it lives in that folder's ``src/``, and nothing outside this
tool folder may import it. The name is specific rather than a bare ``invocation.py``
for the reason ``fence_findings`` gives: a tool's ``src/`` goes on ``sys.path``
as a directory, so a generic module name would collide in a process importing two tools.

Three questions live here, each answered once. Which executable runs when ``--shellcheck``
is left out. What argv a batch of extracted scripts is sent as, and how many batches the
command-line ceiling needs. And what shellcheck's exit status, read beside its parsed
output, says about the run: a verdict, a partial verdict, or no verdict at all.

ShellCheck's exit statuses, from its manual: ``0`` all files checked and nothing found;
``1`` all files checked and something found; ``2`` some files could not be processed;
``3`` bad syntax or options; ``4`` an unknown output format was asked for. A status alone
is not trusted, and neither is the output alone: ``SHELLCHECK_OPTS`` can change the output
format under a status that still reads "findings", and a run whose findings all failed to
parse must not read as clean.

Entry point assumptions
-----------------------
Reached through the ``sys.path`` insert in ``shellcheck_md``, which also puts
``argv_budget`` and this tool's other ``src/`` modules on the path first. Reads nothing but
whether a file exists; starts no process and writes no stream.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import argv_budget
from fence_findings import BatchParse, Finding

# shellcheck's machine-readable output format, and the head of every argv this tool sends
# -- named once so the width charged to each batch is measured from the same list that is
# actually sent (see build_shellcheck_args).
GCC_FORMAT_FLAG = "--format=gcc"

# How many trailing lines of shellcheck's output a could-not-run message quotes: enough to
# show an error and the line before it, few enough that a flood of JSON stays readable.
OUTPUT_TAIL_LINES = 20

# Windows' ERROR_FILENAME_EXCED_RANGE: what CreateProcess returns when the command line is
# too long. CPython raises it as FileNotFoundError -- the SAME class, and the same errno 2,
# as a genuinely absent executable -- so `winerror` is the only thing that separates them,
# and reporting the wrong one sends a reader after a PATH problem that does not exist.
WINERROR_COMMAND_LINE_TOO_LONG = 206

# shellcheck's documented exit statuses that carry a verdict (see the module docstring).
_STATUS_CLEAN = 0
_STATUS_FINDINGS = 1
_STATUS_PARTIAL = 2

# What each status past "partial" means, for the message that names it.
_STATUS_MEANINGS = {3: "bad syntax or options", 4: "an unknown output format"}


class CouldNotRun(Exception):
    """shellcheck ran but reached no verdict this tool can trust: an exit-2 condition.

    *findings* holds what earlier calls in the same run did report, so the caller can print
    them before the error; it is empty when this call was the first to report anything.
    """

    def __init__(self, detail: str, findings: Sequence[Finding] = ()) -> None:
        """Keep *detail* as the message and *findings* for the report."""
        super().__init__(detail)
        self.findings = list(findings)


class IncompleteRun(Exception):
    """shellcheck exited 2: some scripts were not processed, so *findings* is partial.

    Raised only after every batch has run, carrying every finding they produced, so the
    caller can print what WAS found before reporting that the run was incomplete.
    """

    def __init__(self, findings: Sequence[Finding], detail: str) -> None:
        """Keep *findings* for the report and *detail* as the message."""
        super().__init__(detail)
        self.findings = list(findings)


def resolve_default_shellcheck(
    scripts_dir: str | None, *, windows: bool, fallback: str = "shellcheck"
) -> str:
    """The shellcheck to run when ``--shellcheck`` is left out.

    The copy in the running interpreter's own scripts directory comes first: that is
    where the ``shellcheck-py`` package puts the binary, and a pipx install links only
    this tool's own command out of it, so that directory is often not on PATH. Without
    one there, *fallback* is returned as a bare name for the OS to find on PATH.
    """
    if scripts_dir is not None:
        candidate = Path(scripts_dir) / ("shellcheck.exe" if windows else "shellcheck")
        if candidate.is_file():
            return str(candidate)
    return fallback


def build_shellcheck_args(shellcheck: str, script_paths: Sequence[Path]) -> list[str]:
    """The exact argv one shellcheck invocation receives.

    One owner for the command line, because it is both SENT and MEASURED: the splitter
    sizes each batch against this function's output with no paths, so a flag added here
    is charged to every batch automatically instead of quietly eating into the budget. A
    project's rc file travels as a file beside the scripts, not as a flag (see
    ``shellcheck_scratch``).
    """
    return [shellcheck, GCC_FORMAT_FLAG, *(str(p) for p in script_paths)]


def chunk_script_paths(
    script_paths: Sequence[Path],
    *,
    shellcheck: str,
    limit: int,
    headroom: int = argv_budget.DEFAULT_HEADROOM,
) -> list[list[Path]]:
    """Split *script_paths* into batches whose rendered shellcheck argv stays under *limit*.

    The shellcheck-specific half of the split, and the only half that belongs here: this
    tool's own argv head, measured from :func:`build_shellcheck_args` so it matches what is
    emitted. The measuring, the splitting, and the never-drop-a-path rule are
    ``argv_budget``'s -- see ``argv_budget.md``.

    Paths are rendered bare (``str``), which is ``batch_paths``' default: shellcheck takes a
    plain filename, so no path needs a prefix or an escape.
    """
    return argv_budget.batch_paths(
        script_paths,
        prefix_chars=argv_budget.rendered_length(build_shellcheck_args(shellcheck, ())),
        limit=limit,
        headroom=headroom,
    )


def output_tail(output: str) -> str:
    """The last :data:`OUTPUT_TAIL_LINES` lines of *output*, for a could-not-run message.

    Lines end at a newline and nowhere else: ``splitlines`` would also break at U+2028 and
    the other separators, turning one character in ShellCheck's output into a line of the
    tool's own message. The caller escapes each line before printing it.
    """
    stripped = output.strip().replace("\r\n", "\n")
    return "\n".join(stripped.split("\n")[-OUTPUT_TAIL_LINES:]) or "(no output)"


def _status_name(returncode: int) -> str:
    """How a could-not-run message names *returncode*: a signal, or a status and meaning."""
    if returncode < 0:
        return f"was killed by signal {-returncode}"
    meaning = _STATUS_MEANINGS.get(returncode, "an undocumented status")
    return f"exited {returncode} ({meaning})"


def judge_batch(returncode: int, output: str, parsed: BatchParse) -> bool:
    """Decide what one batch's exit status and parsed output say; True when it is partial.

    Raises :class:`CouldNotRun` when the two disagree or the status carries no verdict.
    Returns True for status 2, whose findings are real but incomplete: the caller keeps
    them, runs the remaining batches, and reports the run as incomplete at the end.
    """
    found = len(parsed.findings) + len(parsed.unmapped)
    if returncode == _STATUS_CLEAN and found == 0:
        return False
    if returncode == _STATUS_CLEAN:
        raise CouldNotRun(
            f"shellcheck exited 0, which means it found nothing, yet reported {found} "
            "finding(s); a status and output that disagree is no verdict. Output tail:\n"
            + output_tail(output)
        )
    if returncode == _STATUS_FINDINGS and parsed.findings:
        return False
    if returncode == _STATUS_FINDINGS:
        raise CouldNotRun(
            "shellcheck exited 1, which means it found something, but none of its output "
            "parsed as a gcc-format finding on an extracted script. Is SHELLCHECK_OPTS "
            "changing the output format? Output tail:\n" + output_tail(output)
        )
    if returncode == _STATUS_PARTIAL:
        return True
    raise CouldNotRun(
        f"shellcheck {_status_name(returncode)} without a verdict. Output tail:\n"
        + output_tail(output)
    )


def order_findings(findings: Sequence[Finding]) -> list[Finding]:
    """*findings* by file, line and column: the run's grouping must not set the order."""
    return sorted(findings, key=lambda f: (f.source, f.line, f.column))


def keep_gathered(
    exc: CouldNotRun, findings: Sequence[Finding], partial_tails: Sequence[str]
) -> CouldNotRun:
    """*exc* carrying what earlier groups gathered, so none of it is lost."""
    detail = str(exc)
    if partial_tails:
        detail += (
            "\nAn earlier shellcheck call also exited 2 (some scripts not processed). "
            "Output tail:\n" + "\n".join(partial_tails)
        )
    return CouldNotRun(detail, order_findings(findings))


def could_not_run_message(exc: OSError, shellcheck: str) -> str:
    """The exit-2 diagnostic for a shellcheck invocation that never started.

    Two unrelated faults arrive as ``FileNotFoundError`` carrying the same ``errno``: the
    executable is absent (``winerror`` 2), or the command line was too long for the OS to
    accept (``winerror`` 206). Only the latter attribute separates them, and calling an
    overflow "not found on PATH" is the expensive way to be wrong -- it sends a reader to
    reinstall a backend that is present and working. Any other ``OSError`` (a file that is
    not executable, say) is named with its own text.

    ``getattr`` rather than ``exc.winerror``: the attribute exists only on Windows, and on
    a POSIX host the overflow this names cannot arise -- ``ARG_MAX`` is far larger, and the
    kernel reports ``E2BIG`` rather than through this channel.
    """
    if getattr(exc, "winerror", None) == WINERROR_COMMAND_LINE_TOO_LONG:
        return (
            f"ERROR: shellcheck executable {shellcheck!r} could not be started: the "
            f"command line was too long (WinError {WINERROR_COMMAND_LINE_TOO_LONG}). The "
            "executable is present -- the argv carrying the extracted scripts exceeded "
            "the OS ceiling despite the per-batch split, so report this rather than "
            "reinstalling shellcheck"
        )
    if isinstance(exc, FileNotFoundError):
        return (
            f"ERROR: could not run shellcheck executable {shellcheck!r} "
            "(not found on PATH or at that path)"
        )
    return f"ERROR: could not run shellcheck executable {shellcheck!r}: {exc}"
