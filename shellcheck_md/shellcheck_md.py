#!/usr/bin/env python3
"""Lint shell snippets embedded in Markdown by running them through shellcheck.

Markdown notes routinely embed runnable shell in ``bash`` / ``sh`` fences, yet
shellcheck only understands script files. This module lifts every such block out
to a temporary script (prefixed with a synthesized shebang so shellcheck picks
the right dialect), runs shellcheck over them, and remaps each finding back to its
original ``<markdown file>:<line>``.

Those scripts travel to shellcheck **on the command line**, which has a hard ceiling, so
they are split across as many invocations as it takes to fit and the findings merged. The
split is not a tuning choice: overflowing surfaces on Windows as ``WinError 206``, which
CPython raises as ``FileNotFoundError`` -- the very class a missing executable raises.

Run it from a project root, or from a pre-commit hook: the exit status is non-zero
whenever shellcheck reports a finding, as a linter's is. The entry point assumes a
``shellcheck`` executable is reachable, on PATH or via ``--shellcheck``.

shellcheck's own exit status is read beside its output, never instead of it: a status of
"findings" with no finding that maps back, or a status with no verdict at all, is exit 2
rather than a clean pass (see ``src/shellcheck_invocation.py``).
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import sysconfig
from collections.abc import Sequence
from pathlib import Path
from typing import TextIO

# stdio_utf8 is the shared stream-policy library in a sibling per-tool folder; resolve it
# from this file's own location rather than the working directory, so moving the project
# needs no edit here.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "stdio_utf8"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "help_formatter"))

import help_formatter  # noqa: E402 - import follows the sys.path bootstrap above
import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above

# bounded_run is the shared bounded-capture run seam, resolved the same way: a
# capturing subprocess.run with a timeout does NOT bound a child that spawns its own
# children, because the kill reaches only the direct child.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bounded_run"))

import bounded_run  # noqa: E402 - import follows the sys.path bootstrap above

# nothing_examined is the shared library owning the one wording for "the exclude set
# pruned every candidate, so this result verifies nothing" -- resolved from this file's
# own location like stdio_utf8 above.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nothing_examined"))
# venv_prune owns the name-independent "is this a virtualenv?" test (PEP 405's
# pyvenv.cfg marker), resolved the same sibling way for src/markdown_selection: a
# directory-name prune alone misses a virtualenv not named .venv, and its vendored READMEs
# would be lifted for shellcheck.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "venv_prune"))
# argv_budget owns the command-line ceilings, the list2cmdline measurement and the splitter
# -- so the budget is derived per run from what is actually being launched, rather than
# hardcoded here. Resolved the same sibling way.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "argv_budget"))
# fence_findings and markdown_selection are this tool's OWN private modules, in its `src/`
# dir -- resolved from this file's location for the same reason as the
# siblings above, one level DOWN instead of up. fence_findings holds everything pure: what a
# block and a finding are, and how each crosses between the Markdown and the generated
# script. markdown_selection decides which Markdown files a run examines,
# shellcheck_scratch where the extracted scripts are written, shellcheck_invocation what
# each shellcheck call is sent and what its result means, argv_files_split which
# command-line tokens are options at all, and terminal_safe_parser the parser for them.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

import argv_budget  # noqa: E402 - import follows the sys.path bootstrap above
import argv_files_split  # noqa: E402 - import follows the sys.path bootstrap above
import fence_findings  # noqa: E402 - import follows the sys.path bootstrap above
import markdown_selection  # noqa: E402 - import follows the sys.path bootstrap above
import nothing_examined  # noqa: E402 - import follows the sys.path bootstrap above
import shellcheck_invocation  # noqa: E402 - import follows the sys.path bootstrap above
import shellcheck_scratch  # noqa: E402 - import follows the sys.path bootstrap above
import terminal_safe_parser  # noqa: E402 - import follows the sys.path bootstrap above

# Re-exported because they are this tool's public surface: a caller (and its suite) imports
# `shellcheck_md` and never reaches into a private module. `DEFAULT_LANGS` is re-exported
# rather than restated so --help's fence-language sentence and the post-parse default both
# read the ONE definition -- a second copy here could drift from the code applying it.
DEFAULT_LANGS = fence_findings.DEFAULT_LANGS
ShellBlock = fence_findings.ShellBlock
Finding = fence_findings.Finding
fence_language = fence_findings.fence_language
parse_fence_open = fence_findings.parse_fence_open
closes_fence = fence_findings.closes_fence
dedent_body = fence_findings.dedent_body
extract_blocks = fence_findings.extract_blocks
block_to_script = fence_findings.block_to_script
remap_line = fence_findings.remap_line
remap_column = fence_findings.remap_column
parse_shellcheck_line = fence_findings.parse_shellcheck_line
script_name = fence_findings.script_name
_norm = fence_findings._norm
_findings_from_output = fence_findings._findings_from_output
ConfigError = markdown_selection.ConfigError
read_config_excludes = markdown_selection.read_config_excludes
should_exclude = markdown_selection.should_exclude
discover_markdown_files = markdown_selection.discover_markdown_files
select_named_files = markdown_selection.select_named_files
nearest_rcfile = markdown_selection.nearest_rcfile
read_shell_blocks = markdown_selection.read_shell_blocks
UnreadableMarkdown = markdown_selection.UnreadableMarkdown
GCC_FORMAT_FLAG = shellcheck_invocation.GCC_FORMAT_FLAG
OUTPUT_TAIL_LINES = shellcheck_invocation.OUTPUT_TAIL_LINES
WINERROR_COMMAND_LINE_TOO_LONG = shellcheck_invocation.WINERROR_COMMAND_LINE_TOO_LONG
CouldNotRun = shellcheck_invocation.CouldNotRun
IncompleteRun = shellcheck_invocation.IncompleteRun
build_shellcheck_args = shellcheck_invocation.build_shellcheck_args
chunk_script_paths = shellcheck_invocation.chunk_script_paths
could_not_run_message = shellcheck_invocation.could_not_run_message
output_tail = shellcheck_invocation.output_tail
order_findings = shellcheck_invocation.order_findings
keep_gathered = shellcheck_invocation.keep_gathered
resolve_default_shellcheck = shellcheck_invocation.resolve_default_shellcheck
ScratchWriteError = shellcheck_scratch.ScratchWriteError
split_at_files = argv_files_split.split_at_files

# Per-BATCH wall-clock bound: the extracted scripts are split across as many shellcheck
# invocations as the argv ceiling requires, and this bounds EACH of them -- so a list split
# three ways can take up to three times this in total. shellcheck is a fast native binary,
# so a whole tree of Markdown fences is seconds and this is headroom for a cold filesystem
# rather than a tight SLA; even several batches of it stay inside the bound a build or a
# pre-commit run would put around the whole tool, so a WEDGED shellcheck surfaces as this
# tool's own exit 2 naming shellcheck rather than a generic kill from outside. The bound
# must be on the call: an outer deadline only fires between calls, never inside one.
DEFAULT_TIMEOUT_SECONDS = 300.0

# The backend this gate drives, named ONCE. lint_markdown's default, the CLI's default
# and the ceiling each batch is sized against have to name the SAME executable, or the
# budget is measured over a command line the tool never sends.
DEFAULT_SHELLCHECK = "shellcheck"


def _run_shellcheck(
    shellcheck: str, script_paths: Sequence[Path], timeout: float
) -> tuple[int, str]:
    """Run shellcheck in gcc format over `script_paths`; return (exit status, output).

    *script_paths* is ONE batch (see :func:`chunk_script_paths`), all in one folder with
    one rc file, and *timeout* bounds this invocation alone. The output is stdout then stderr. Raises FileNotFoundError when the executable
    cannot be found -- or, on Windows, when the command line is too long, which arrives as
    the same class and is told apart by ``winerror`` -- another ``OSError`` when it cannot
    be started at all, and ``subprocess.TimeoutExpired`` after *timeout* seconds. The
    caller maps all of them to the same exit-2 "could not run" rather than a false clean.
    """
    completed = bounded_run.run_bounded(
        build_shellcheck_args(shellcheck, script_paths),
        timeout=timeout,
    )
    return completed.returncode, completed.stdout + completed.stderr


def _run_group(
    scripts: Sequence[Path],
    path_to_block: dict[str, ShellBlock],
    shellcheck: str,
    timeout: float,
) -> tuple[list[Finding], list[str]]:
    """Run one rc-file group in as many batches as the argv ceiling needs.

    Returns the findings and, for each batch that exited 2, its output tail. Raises
    :class:`CouldNotRun` for a batch with no verdict (see ``judge_batch``).
    """
    findings: list[Finding] = []
    partial_tails: list[str] = []
    for batch in chunk_script_paths(
        scripts,
        shellcheck=shellcheck,
        limit=argv_budget.argv_limit_for(shellcheck),
    ):
        returncode, output = _run_shellcheck(shellcheck, batch, timeout)
        # Parsed per batch rather than over one concatenated blob: an output not ending
        # in a newline would otherwise weld its last line onto the next batch's first.
        parsed = _findings_from_output(output, path_to_block)
        if shellcheck_invocation.judge_batch(returncode, output, parsed):
            partial_tails.append(output_tail(output))
        findings.extend(parsed.findings)
    return findings, partial_tails


def lint_markdown(
    md_files: Sequence[Path],
    *,
    langs: Sequence[str] = DEFAULT_LANGS,
    shellcheck: str = DEFAULT_SHELLCHECK,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> list[Finding]:
    """Extract shell blocks from `md_files`, shellcheck them, return findings in order.

    The generated scripts are grouped by the rc file nearest each Markdown file, each group
    in its own temporary folder beside a copy of that rc file (see ``shellcheck_scratch``),
    and each group is split across as many shellcheck invocations as the argv ceiling
    requires (:func:`chunk_script_paths`); *timeout* applies to EACH invocation rather
    than to the whole set. Findings from every batch are accumulated and returned sorted by
    file, line and column, whatever the grouping.

    Raises :class:`UnreadableMarkdown` before running anything when a file cannot be read,
    and :class:`ScratchWriteError` when the scripts cannot be written. A missing shellcheck
    raises FileNotFoundError, one that cannot start another ``OSError``, one that outlasts
    *timeout* ``subprocess.TimeoutExpired``, and one whose exit status carries no verdict
    :class:`CouldNotRun`, carrying whatever earlier groups found -- all could-not-run
    conditions the caller reports as exit 2, and all abandon the remaining batches: an
    environment fault the next batch would only reproduce. A batch exiting 2 does not stop
    the run; after every batch has run, :class:`IncompleteRun` carries every finding found.
    """
    blocks = read_shell_blocks(md_files, langs)
    if not blocks:
        return []
    findings: list[Finding] = []
    partial_tails: list[str] = []
    with shellcheck_scratch.scratch_directory() as tmpdir:
        # nearest_rcfile is looked up here, at call time, so a test can bound its walk.
        path_to_block, groups = shellcheck_scratch.stage_scripts(
            blocks, Path(tmpdir), nearest_rcfile
        )
        for scripts in groups:
            try:
                group_findings, tails = _run_group(
                    scripts, path_to_block, shellcheck, timeout
                )
            except CouldNotRun as exc:
                if findings or partial_tails:
                    raise keep_gathered(exc, findings, partial_tails) from exc
                raise
            findings.extend(group_findings)
            partial_tails.extend(tails)
    if partial_tails:
        raise IncompleteRun(
            order_findings(findings),
            "shellcheck exited 2: some extracted scripts could not be processed, so the "
            "findings reported are incomplete. Output tail:\n"
            + "\n".join(partial_tails),
        )
    return order_findings(findings)


def _report(
    findings: Sequence[Finding],
    file_count: int,
    out: TextIO,
    hidden: int = 0,
    flag: str = "--root",
) -> None:
    """Print findings (one per line) and a one-line summary to `out`.

    *flag* names where the candidates came from, for the ``NOTHING EXAMINED`` caveat. A
    finding's file name and message come from outside the tool, so each line is escaped
    (:func:`stdio_utf8.spell_unsafe`) before it reaches a terminal.
    """
    for finding in findings:
        line = (
            f"{finding.source}:{finding.line}:{finding.column}: "
            f"{finding.severity}: {finding.message} [{finding.code}]"
        )
        out.write(stdio_utf8.spell_unsafe(line) + "\n")
    if caveat := nothing_examined.note(
        hidden, noun="Markdown file", table="shellcheck_md", flag=flag
    ):
        print(caveat, file=out)
    out.write(
        f"Checked {file_count} Markdown file(s): "
        f"{len(findings)} shellcheck finding(s).\n"
    )


def _select(args: argparse.Namespace) -> tuple[list[Path], int]:
    """The Markdown files this run examines, and how many candidates the excludes hid.

    A run that examined NOTHING must not read as verified-clean, so the hidden count is
    what ``nothing_examined`` needs to say so. Raises :class:`ConfigError`.
    """
    cli_excludes = [item.resolve() for item in args.exclude]
    if args.files:
        md_files = select_named_files(args.files, cli_excludes)
        return md_files, nothing_examined.hidden_count(
            len(md_files), lambda: len({path.resolve() for path in args.files})
        )
    root = Path(args.root)
    # Read up front as well, so config above --root that cannot be trusted is exit 2 even
    # when no file is found under it to walk up from.
    read_config_excludes(root)
    candidates = discover_markdown_files(root, args.glob, ())
    # The same per-file exclude rule --files applies, so the two modes mean the same: each
    # discovered file answers to its own nearest configuring pyproject, not the root's.
    md_files = select_named_files(candidates, cli_excludes)
    return md_files, nothing_examined.hidden_count(
        len(md_files), lambda: len(candidates)
    )


def _interpreter_scripts_dir() -> str | None:
    """The running interpreter's scripts directory: ``bin`` on POSIX, ``Scripts`` on Windows.

    A seam, so a test can point the default-executable lookup at a directory it controls.
    """
    return sysconfig.get_path("scripts")


_EPILOG = (
    "Prerequisite: a shellcheck executable. Without --shellcheck, the one installed beside "
    "this Python (where the shellcheck-py package puts it) is used if present, else "
    "shellcheck on PATH. Give either --root, to lint every Markdown file found under it, "
    "or --files, to lint the files named; not both. --files must be last: every argument "
    "after it is a file name, never an option, so a pre-commit hook ends its command "
    "with --files and the staged files it appends are the ones linted. Each Markdown "
    "file's nearest .shellcheckrc (or shellcheckrc) is copied beside the scripts lifted "
    "from it, where shellcheck finds it; SHELLCHECK_OPTS still applies, and one that changes the output format is a "
    "could-not-run, never a clean result. "
    "The extracted scripts are split across as many shellcheck calls as the "
    "command-line ceiling requires, and --timeout-seconds bounds EACH of them, so "
    "a split run can take a multiple of it; exceeding it is the same "
    "could-not-run (exit 2) as a missing executable, never a clean result. "
    "Exit codes: 0 = clean; 1 = shellcheck findings reported; 2 = untrusted "
    "pyproject config ([tool.shellcheck_md] malformed or unreadable), a "
    "--files entry that is not a file, a Markdown file that cannot be read as UTF-8, "
    "a temporary script that cannot be written, or shellcheck could not be run or reached no verdict (missing, unable to start, "
    "over the bound, or an exit status of 2 or more -- the findings already reported are "
    "printed first)."
)


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI (named options only, defaults shown in --help).

    Split out of :func:`main` so the parser is reachable without running the tool: the
    formatter binding is then assertable directly, instead of only through rendered
    ``--help`` text -- which on a 3.13+ interpreter cannot falsify it at all.
    """
    parser = terminal_safe_parser.TerminalSafeParser(
        prog=os.path.basename(sys.argv[0]),
        description=(
            "Extract fenced bash/sh blocks from Markdown and lint them with shellcheck."
        ),
        # Hides the None and [] defaults of the options below: neither is a value anyone
        # could type, and each option's help says what leaving it out means instead.
        formatter_class=help_formatter.UnsetHidingFormatter,
        epilog=_EPILOG,
        # Defense in depth beside the split in main: no prefix of an option is accepted
        # as that option, so a token can only ever mean the option it spells out.
        allow_abbrev=False,
    )
    parser.add_argument(
        "--root", help="Directory to search for Markdown files (or give --files)."
    )
    # Declared for --help only: main splits the command line at this flag before parsing,
    # so argparse never sees it, nor any token after it (see argv_files_split).
    parser.add_argument(
        "--files",  # spelled as argv_files_split.FILES_FLAG is; a test pins the two
        nargs="+",
        default=[],
        metavar="FILE",
        help=(
            "Markdown files to lint instead of searching --root. Must be last: every "
            "argument after it is a file name."
        ),
    )
    parser.add_argument(
        "--glob",
        default="**/*.md",
        help="Glob, relative to --root, selecting Markdown files.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        type=Path,
        metavar="PATH",
        help="File or directory to skip (repeatable); none when omitted.",
    )
    parser.add_argument(
        "--shellcheck",
        default=None,
        help=(
            "shellcheck executable: a name on PATH or a full path. Omitted, the one "
            "beside this Python is used if present, else shellcheck on PATH."
        ),
    )
    parser.add_argument(
        "--timeout-seconds",
        type=float,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=(
            "Wall-clock bound on EACH shellcheck call (the scripts are split across as "
            "many as the argv ceiling needs); exceeding it is exit 2, not clean."
        ),
    )
    parser.add_argument(
        "--lang",
        action="append",
        dest="langs",
        metavar="LANG",
        default=None,
        # The real default is applied after parsing (DEFAULT_LANGS when no --lang
        # is given), so the value derives from the constant here -- it cannot drift
        # from a hand-typed "bash and sh".
        help=(
            "Fence language to lint, matched against a fence's first info-string word "
            "without case; repeatable. Any value replaces the built-in "
            f"set ({', '.join(DEFAULT_LANGS)}); omitting it keeps that set."
        ),
    )
    return parser


def _print_error(text: str) -> None:
    """Print *text* to stderr with every line escaped for a terminal.

    The messages quote file names and ShellCheck's own output, neither written by this
    tool, so each line goes through :func:`stdio_utf8.spell_unsafe`; the newlines the tool
    put between them stay real line breaks.
    """
    lines = text.split("\n")
    print("\n".join(stdio_utf8.spell_unsafe(line) for line in lines), file=sys.stderr)


def _lint_and_report(
    args: argparse.Namespace, md_files: Sequence[Path], hidden: int
) -> int:
    """Lint *md_files* and print the report; return the exit status.

    Every way the run can fail to reach a verdict is exit 2 here, never 1 (which means
    findings) and never a traceback.
    """
    shellcheck: str = args.shellcheck or resolve_default_shellcheck(
        _interpreter_scripts_dir(), windows=os.name == "nt", fallback=DEFAULT_SHELLCHECK
    )
    flag = "the named files" if args.files else "--root"
    try:
        findings = lint_markdown(
            md_files,
            langs=tuple(args.langs) if args.langs else DEFAULT_LANGS,
            shellcheck=shellcheck,
            timeout=args.timeout_seconds,
        )
    except (UnreadableMarkdown, ScratchWriteError) as exc:
        _print_error(f"ERROR: {exc}")
        return 2
    except IncompleteRun as exc:
        # The findings shellcheck did report are real, so they are printed; the run is
        # still exit 2, because the scripts it could not process were never checked.
        _report(exc.findings, len(md_files), sys.stdout, hidden, flag)
        _print_error(f"ERROR: {exc}")
        return 2
    except CouldNotRun as exc:
        # Findings earlier groups reported are real too, and printed the same way.
        if exc.findings:
            _report(exc.findings, len(md_files), sys.stdout, hidden, flag)
        _print_error(f"ERROR: could-not-run: {exc}")
        return 2
    except OSError as exc:
        # A shellcheck that could not be launched is "could not run" (exit 2), never a false
        # clean or a traceback. Only reached when a bash/sh fence is
        # present (else shellcheck is never invoked). Which fault it was -- absent, an
        # over-long command line, or not startable at all -- is could_not_run_message's
        # question, because the first two are indistinguishable by class.
        _print_error(could_not_run_message(exc, shellcheck))
        return 2
    except subprocess.TimeoutExpired:
        # Same exit-2 could-not-run channel: shellcheck never finished, so no verdict
        # was reached and reporting clean would be a false one.
        _print_error(
            f"ERROR: shellcheck exceeded the {args.timeout_seconds:g}s bound and was "
            "killed; it never finished linting, so this is a could-not-run"
        )
        return 2
    _report(findings, len(md_files), sys.stdout, hidden, flag)
    return 1 if findings else 0


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point: lint embedded shell and return 1 if any findings."""
    # Piped stdout inherits the locale code page (cp1252 on Windows), which cannot
    # encode a non-ASCII discovered path; force UTF-8 before anything is written.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    # Only tokens before --files are options: a staged name cannot become one.
    options, names = split_at_files(sys.argv[1:] if argv is None else argv)
    args = parser.parse_args(options)
    argv_files_split.attach_files(parser, args, names)
    if missing := markdown_selection.missing_files(args.files):
        _print_error(f"ERROR: not a file: {', '.join(map(str, missing))}")
        return 2
    try:
        md_files, hidden = _select(args)
    except ConfigError as exc:
        _print_error(f"ERROR: {exc}")
        return 2
    return _lint_and_report(args, md_files, hidden)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
