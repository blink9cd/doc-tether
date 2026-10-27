#!/usr/bin/env python3
"""Catch doc-vs-code drift in Python argparse CLI tools, deterministically.

Point this at a ``.py`` file or a directory (``--path``). It discovers Python
CLI files (a ``.py`` that constructs an ``argparse.ArgumentParser``) and, for
each, runs the checks below, which lint / tests / coverage do *not* catch. Each CLI
is paired with its sibling ``<name>.md``, or, for a single-file ``--path``, with the
doc ``--doc`` names (a README, say).

* **Check A -- hardcoded default (AST only).** A ``help=`` string / f-string
  literal that contains ``"(default"`` but no ``%(default)`` token: argparse's
  ``ArgumentDefaultsHelpFormatter`` appends its own ``(default: X)``, so the
  hand-written one double-prints and drifts.
* **Check B -- the doc mirrors the CLI (AST; a live ``--help`` only on request).**
  The doc must document every option, and must not document one that does not
  exist, in an arguments table or a pasted ``--help`` block (a fenced block whose
  first line starts with ``usage:``). It is deliberately *conservative*: it
  hard-fails only on an unambiguous mismatch (an option present in the parser but
  absent from the doc, or vice-versa; with ``--run-help``, a purely-numeric default
  that clearly contradicts; a doc with neither a table nor a block). Anything it
  cannot parse with confidence -- a paraphrased default, exit codes -- is reported
  as advisory.
* **Check C -- stated floor vs imports.** A module that imports a
  version-gated stdlib module (e.g. ``tomllib`` needs 3.11) while its docstring,
  epilog, or ``.md`` states a *lower* ``3.x+`` floor is flagged at every stale
  location.
* **Check D -- no positional arguments (AST only; opt-in).** With
  ``--check-positionals``, an ``add_argument`` whose first argument is a string
  literal that does not start with ``-`` defines a positional argument, and
  user-facing CLI arguments should be named options, so it is flagged. A first
  argument that is a keyword or a non-literal (a variable/expr) cannot be proven
  positional and is skipped.

**Execution.** ``--run-help`` (or ``run_help = true`` in
``[tool.cli_doc_consistency]``) runs ``<python> <tool> --help`` for each CLI to compare
documented defaults; without it every defaults comparison is skipped, and one advisory
per doc says so. That config is read from the nearest ``pyproject.toml`` above
``--path``, which is inside the scanned tree, so an untrusted tree can turn execution
on by itself: ``--no-run-help`` beats both the flag and the key, and is how to scan a
tree you do not trust. ``--check-positionals`` / ``check_positionals = true`` turns
Check D on; that flag cannot turn the key off.

A discovered CLI that ships without its companion ``<name>.md`` is a *gating*
Check-B finding (pass ``--allow-missing-doc`` to downgrade it to advisory).

Exit ``0`` (clean) / ``1`` (a gating finding) / ``2`` (could not run: missing or
unparseable ``--path``, a ``--doc`` with a directory ``--path`` or naming no file,
undecodable target or doc, malformed config). A gating finding is never reported as
a pass, and a run that examined no CLI prints ``NOTHING EXAMINED`` rather than passing
silently; Check B's conservative rules mean a drift it cannot read with confidence is
advisory, not gating. Output is ASCII-only so a redirected console cannot crash on a
non-ASCII byte.

Its config is read by ``pyproject_config``, which imports ``tomllib``, so this tool
requires Python 3.11+.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

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
# own location like stdio_utf8 above. The report renderer needs it
# too and bootstraps it again, so that module imports standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nothing_examined"))

import nothing_examined  # noqa: E402 - import follows the sys.path bootstrap above

# The clidoc_* modules below are this tool's OWN private modules, in its `src/` dir --
# resolved from this file's location one level DOWN, unlike the
# siblings above. venv_prune and pyproject_config are not bootstrapped here: clidoc_targets,
# their only caller, bootstraps them itself.
sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

# The shared vocabulary. RenderHelpFn and Finding are used below; the rest are re-exported
# because the suites drive them as cli_doc_consistency.<name>.
from clidoc_model import (  # noqa: E402,F401 - E402 follows the bootstrap; F401 are re-exports
    CHECK_A,
    CHECK_B,
    CHECK_C,
    CHECK_D,
    SEVERITY_ADVISORY,
    SEVERITY_GATING,
    Finding,
    MdArgTable,
    OptionInfo,
    RenderFailure,
    RenderHelpFn,
    TargetError,
)

# Scope: the config read and the discovery walk. ConfigError, _read_source, _read_text,
# _MD_SUFFIX, _PY_SUFFIX, discover_cli_files and read_config_excludes are all used below.
from clidoc_targets import (  # noqa: E402 - import follows the sys.path bootstrap above
    _MD_SUFFIX,
    _PY_SUFFIX,
    _read_source,
    _read_text,
    ConfigError,
    ConfigSwitches,
    discover_cli_files,
    parse_source,
    read_config_excludes,
    read_config_switches,
)

# The AST reader. None is called here -- every name is a re-export for the suites.
from clidoc_astcli import (  # noqa: E402,F401 - E402 follows the bootstrap; F401 are re-exports
    _int_constants,
    argument_group_titles,
    _is_sys_exit,
    ast_exit_codes,
    cli_options_from_ast,
    help_literal_text,
    is_cli_module,
    names_argument_parser,
)

# Checks A and D. Both are called by analyze_file below.
from clidoc_astchecks import (  # noqa: E402 - import follows the sys.path bootstrap above
    check_hardcoded_defaults,
    check_positional_arguments,
)

# Check C. Only check_floor is called here; the rest are re-exports for the suites, which
# drive the two floor readings independently of the check that compares them.
from clidoc_floor import (  # noqa: E402,F401 - E402 follows the bootstrap; F401 are re-exports
    VERSION_GATED_STDLIB,
    _epilog_node,
    _floor_claim_texts,
    _is_formatter_class,
    _leading_docstring,
    _top_module,
    _version_str,
    check_floor,
    collect_imports,
    implied_floor,
    stated_floors,
)

# Check B's artifact parsers. None is called here -- every name is a re-export.
from clidoc_docread import (  # noqa: E402,F401 - E402 follows the bootstrap; F401 are re-exports
    _build_arg_table,
    _default_column_index,
    _documented_names,
    _is_arg_header,
    _is_separator_cells,
    _is_table_row,
    _MAX_HELP_ENTRY_CHARS,
    _iter_help_entries,
    _option_default_cell,
    _options_block_start,
    _section_after_heading,
    _split_row,
    parse_help_defaults,
    parse_md_arg_tables,
    parse_md_exit_codes,
    parse_md_help_blocks,
)

# Check B's comparison. Only check_md_mirror is called here.
from clidoc_mirror import (  # noqa: E402,F401 - E402 follows the bootstrap; F401 are re-exports
    _cell_number,
    _coverage_findings,
    _default_findings,
    _exit_code_findings,
    _number_str,
    _option_default_number,
    check_md_mirror,
)

# Verdict rendering. Only format_report is called here.
from clidoc_report import (  # noqa: E402,F401 - E402 follows the bootstrap; F401 are re-exports
    _format_finding,
    _verdict_name,
    format_report,
)


# Per-call timeout for the live --help render, so a hung tool cannot wedge the gate.
_HELP_TIMEOUT_SECONDS = 30.0


def _ascii_line(text: str) -> str:
    """*text* as one stripped ASCII line: non-ASCII escaped, so any console can print it."""
    return text.strip().encode("ascii", "backslashreplace").decode("ascii")


def _failed_render(returncode: int, stderr: str) -> RenderFailure:
    """A failed render's reason: its exit status and first non-blank stderr line."""
    first = next((line for line in stderr.splitlines() if line.strip()), "")
    reason = f"exit status {returncode}"
    return RenderFailure(f"{reason}: {_ascii_line(first)}" if first else reason)


def _render_help(tool_path: Path, python: str) -> str | RenderFailure:
    """Run ``<python> <tool_path> --help`` and return its stdout, or why it failed.

    This EXECUTES the target, which is why only ``--run-help`` / ``run_help`` reaches it.

    A missing interpreter, an import error in the tool, or a timeout all yield a
    :class:`RenderFailure` so Check B falls back to the source's literal defaults, and
    says so, rather than crashing.
    """
    try:
        # Absolute, because the child runs in the tool's folder: a caller-relative path
        # (every relative --path) would name a file that is not there from that cwd.
        proc = bounded_run.run_bounded(
            [python, str(tool_path.resolve()), "--help"],
            timeout=_HELP_TIMEOUT_SECONDS,
            cwd=str(tool_path.parent),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return RenderFailure(f"could not run: {_ascii_line(type(exc).__name__)}")
    if proc.returncode != 0:
        return _failed_render(proc.returncode, proc.stderr)
    return proc.stdout


def analyze_file(
    py_path: Path,
    md_path: Path,
    tree: ast.Module,
    md_text: str | None,
    *,
    render_help: RenderHelpFn,
    python: str,
    allow_missing_doc: bool = False,
    run_help: bool = False,
    check_positionals: bool = False,
) -> list[Finding]:
    """Run the checks over one CLI file and return the combined findings.

    Check D runs only with ``check_positionals``; Check B executes the target (through
    *render_help*) only with ``run_help``.
    """
    findings = check_hardcoded_defaults(tree, py_path)
    if check_positionals:
        findings.extend(check_positional_arguments(tree, py_path))
    findings.extend(check_floor(tree, py_path, md_path, md_text))
    findings.extend(
        check_md_mirror(
            py_path,
            md_path,
            tree,
            md_text,
            render_help=render_help,
            python=python,
            allow_missing_doc=allow_missing_doc,
            run_help=run_help,
        )
    )
    return findings


def build_parser() -> argparse.ArgumentParser:
    """Named-options-only CLI (prog from argv[0], never a literal); defaults shown in --help."""
    parser = argparse.ArgumentParser(
        description="Catch doc-vs-code drift in Python argparse CLI tools.",
        formatter_class=help_formatter.ReqAwareDefaultsFormatter,
        epilog=(
            "Runs standalone (Python 3.11+, for the stdlib tomllib). Discovers CLI "
            "files (a .py that constructs argparse.ArgumentParser), pairs each with "
            "its sibling <name>.md (or the --doc named for a single-file --path), and "
            "runs Check A (hardcoded help default), Check B (the doc's arguments "
            "tables and pasted --help blocks mirror the parser's options, read from "
            "the AST; a missing companion doc gates unless --allow-missing-doc is "
            "passed), and Check C (stated floor vs version-gated imports). Two "
            "opt-ins, each a flag or a [tool.cli_doc_consistency] key: --run-help / "
            "run_help EXECUTES '<python> <tool> --help' to compare documented "
            "defaults. That config is the nearest pyproject.toml above --path, so the "
            "scanned tree's own config can turn execution on: scan a tree you do not "
            "trust with --no-run-help, which beats both. --check-positionals / "
            "check_positionals adds Check D (no positional CLI arguments). The "
            "nearest pyproject.toml at or above --path supplies that table, whose "
            "exclude is unioned with any --exclude. Exit codes: 0 = clean, 1 = a "
            "gating finding, 2 = could not run (bad --path or --doc, or malformed "
            "config)."
        ),
    )
    parser.add_argument(
        "--path",
        required=True,
        help="A Python CLI file, or a directory to walk for CLI files.",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="PATH",
        help="File/dir to skip while walking (repeatable); unioned with the pyproject table.",
    )
    parser.add_argument(
        "--json", action="store_true", help="Emit a machine-readable JSON report."
    )
    parser.add_argument(
        "--allow-missing-doc",
        action="store_true",
        help="Downgrade a missing companion .md from gating to advisory.",
    )
    parser.add_argument(
        "--doc",
        metavar="PATH",
        help="The doc to check instead of the sibling <name>.md; --path must name "
        "one CLI file.",
    )
    # Mutually exclusive: asking for execution and refusing it at once is a usage error,
    # not a precedence question.
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--run-help",
        action="store_true",
        help="EXECUTE '<python> <tool> --help' to compare documented defaults "
        "(also on with run_help = true in the config).",
    )
    execution.add_argument(
        "--no-run-help",
        action="store_true",
        help="Never execute the target, whatever the scanned tree's own config says; "
        "use it to scan a tree you do not trust.",
    )
    parser.add_argument(
        "--check-positionals",
        action="store_true",
        help="Flag positional CLI arguments, Check D (also on with "
        "check_positionals = true in the config).",
    )
    return parser


def _analyze_targets(
    targets: Sequence[Path],
    python: str,
    *,
    switches: ConfigSwitches = ConfigSwitches(run_help=False, check_positionals=False),
    doc: Path | None = None,
    allow_missing_doc: bool = False,
) -> dict[Path, list[Finding]]:
    """Read, parse, and analyze each discovered CLI file against its doc.

    The doc is *doc* when given (``main`` allows it only for a single-file ``--path``),
    else the sibling ``<name>.md``.
    """
    results: dict[Path, list[Finding]] = {}
    for py_path in targets:
        tree = parse_source(py_path, _read_source)
        md_path = doc if doc is not None else py_path.with_suffix(_MD_SUFFIX)
        md_text = _read_doc(md_path) if md_path.is_file() else None
        results[py_path] = analyze_file(
            py_path,
            md_path,
            tree,
            md_text,
            render_help=_render_help,
            python=python,
            allow_missing_doc=allow_missing_doc,
            run_help=switches.run_help,
            check_positionals=switches.check_positionals,
        )
    return results


def _read_doc(md_path: Path) -> str:
    """The doc's text; one that is not UTF-8 is a :class:`TargetError` (exit 2)."""
    try:
        return _read_text(md_path)
    except UnicodeDecodeError as exc:
        raise TargetError(f"cannot decode {md_path} as UTF-8: {exc}") from exc


def _discover(path: Path, excludes: Sequence[Path]) -> tuple[list[Path], int]:
    """The CLI files under *path*, and how many the excludes hid when none remain.

    A run that examined NOTHING must not read as verified-clean, so the hidden count
    re-runs discovery unfiltered (see nothing_examined). Raises :class:`TargetError`.
    """
    targets = discover_cli_files(path, excludes, read_text=_read_source)
    hidden = nothing_examined.hidden_count(
        len(targets),
        lambda: len(discover_cli_files(path, (), read_text=_read_source)),
    )
    return targets, hidden


def _usage_error(path: Path, doc: str | None) -> str | None:
    """Why *path* / *doc* cannot be checked as given, or None when they can."""
    if not path.exists():
        return f"--path does not exist: {path}"
    if path.is_file() and path.suffix != _PY_SUFFIX:
        return f"--path is not a Python file: {path}"
    if doc is None:
        return None
    if not path.is_file():
        return f"--doc needs --path to name a single CLI file, not a directory: {path}"
    if not Path(doc).is_file():
        return f"--doc does not exist: {doc}"
    return None


def _effective_switches(args: argparse.Namespace, path: Path) -> ConfigSwitches:
    """The config's opt-ins, each also turned on by its flag; ``--no-run-help`` beats both.

    The config is the nearest pyproject above ``--path``, which sits INSIDE the scanned
    tree, so that tree can switch execution on by itself. ``--no-run-help`` is the
    caller's override that the tree cannot undo.

    Raises :class:`ConfigError` on a malformed config, as the exclude read does.
    """
    config = read_config_switches(path)
    return ConfigSwitches(
        run_help=not args.no_run_help and (config.run_help or args.run_help),
        check_positionals=config.check_positionals or args.check_positionals,
    )


def main(argv: list[str] | None = None) -> int:
    """Discover CLI files, run the checks, report; return 0/1/2."""
    # Piped stdout inherits the locale code page (cp1252 on Windows), which cannot
    # encode a non-ASCII discovered path; force UTF-8 before anything is written.
    stdio_utf8.force_utf8_streams(sys.stdout, sys.stderr)
    parser = build_parser()
    args = parser.parse_args(argv)
    path = Path(args.path)
    problem = _usage_error(path, args.doc)
    if problem is not None:
        print(f"ERROR: {problem}", file=sys.stderr)
        return 2
    try:
        config_excludes = read_config_excludes(path)
        switches = _effective_switches(args, path)
    except ConfigError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    excludes = [*config_excludes, *(Path(e).resolve() for e in args.exclude)]
    try:
        targets, hidden = _discover(path, excludes)
        if path.is_file() and not targets:
            raise TargetError(
                f"--path is not an argparse CLI (no ArgumentParser found): {path}"
            )
        results = _analyze_targets(
            targets,
            sys.executable,
            switches=switches,
            doc=Path(args.doc) if args.doc is not None else None,
            allow_missing_doc=args.allow_missing_doc,
        )
    except TargetError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(format_report(results, parser.prog, as_json=args.json, hidden=hidden))
    gating = any(f.severity == SEVERITY_GATING for fs in results.values() for f in fs)
    return 1 if gating else 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    sys.exit(main())
