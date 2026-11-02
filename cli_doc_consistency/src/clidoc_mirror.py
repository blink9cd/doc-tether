"""Check B: compare the parser, the rendered help and the ``.md``, and emit findings.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

The readers live in ``clidoc_astcli`` and ``clidoc_docread``; what is left here is the
judgment, which is the part with a policy in it. Four rules carry that policy. An option
in the parser but not the doc's tables and pasted ``--help`` blocks (or the reverse) is an
unambiguous mismatch and gates. A default that both sides state *numerically* and state
differently also gates, because a number cannot be a paraphrase. But reading the real
default means executing the target, so that rule runs only when the caller opts in, and
says so in one advisory when it did not. A doc with no option surface at all (no table,
no pasted ``--help`` block) gates too, since coverage then verifies nothing. Everything
else (a prose default, an exit-code difference) is advisory, because the tool cannot
tell a real drift from a doc written in a style it does not parse, and a gate that
guesses gets switched off.

That asymmetry is why the finding builders are grouped by what they compare rather than by
severity: the severity is a conclusion each rule reaches, not a category the code is
organized around.

Entry point assumptions
-----------------------
Pure functions over an ``ast`` tree, already-read text, and the injected render seam: no
filesystem reads, no subprocesses of its own, no streams, no sibling libraries. The seam's
implementation stays in the parent, where the suite pins it. Importing this module on its
own needs no import order.
"""

from __future__ import annotations

import ast
import math
import re
from collections.abc import Sequence
from pathlib import Path

from clidoc_astcli import argument_group_titles, ast_exit_codes, cli_options_from_ast
from clidoc_docread import (
    _documented_names,
    _option_default_cell,
    parse_help_defaults,
    parse_md_arg_tables,
    parse_md_help_blocks,
    parse_md_exit_codes,
)
from clidoc_model import (
    CHECK_B,
    SEVERITY_ADVISORY,
    SEVERITY_GATING,
    Finding,
    MdArgTable,
    OptionInfo,
    RenderFailure,
    RenderHelpFn,
)


def _cell_number(cell: str) -> float | None:
    """Parse *cell* as a pure number (ignoring backticks and a trailing ``%``), else None.

    Deliberately strict: a prose cell ("derived", "current interpreter") returns
    None, so a stray digit inside prose can never look like a numeric default.
    """
    text = cell.strip().strip("`").strip().rstrip("%").strip()
    return float(text) if re.fullmatch(r"-?\d+(?:\.\d+)?", text) else None


def _option_default_number(
    option: OptionInfo, rendered: dict[str, str]
) -> float | None:
    """The option's numeric default, from the live render if present, else the AST."""
    if option.name in rendered:
        return _cell_number(rendered[option.name])
    if option.has_literal_default and isinstance(option.literal_default, (int, float)):
        if isinstance(option.literal_default, bool):
            return None
        return float(option.literal_default)
    return None


def _coverage_findings(
    options: Sequence[OptionInfo], tables: Sequence[MdArgTable], md_path: Path
) -> list[Finding]:
    """Gating findings for options missing from the doc, or documented but undefined.

    Options are matched against the *union* of every arguments table, so a
    subparser tool that documents its options across per-subcommand tables is not
    falsely flagged.
    """
    findings: list[Finding] = []
    # Any long spelling of an option covers it, and any spelling is a defined name.
    all_names = {name for o in options for name in o.names}
    md_names = _documented_names(tables)
    for option in options:
        if option.hidden or md_names.intersection(option.names):
            continue
        findings.append(
            Finding(
                CHECK_B,
                SEVERITY_GATING,
                str(md_path),
                None,
                option.name,
                f"CLI option {option.name} is not documented in the doc's arguments "
                "table or --help block",
                f"document {option.name} in the doc (a table row, or the pasted "
                "--help block)",
            )
        )
    for name in sorted(md_names):
        if name not in all_names:
            findings.append(
                Finding(
                    CHECK_B,
                    SEVERITY_GATING,
                    str(md_path),
                    None,
                    name,
                    f"the doc documents option {name}, which the CLI does not define",
                    f"remove {name} from the doc, or add it to the parser",
                )
            )
    return findings


def _default_findings(
    options: Sequence[OptionInfo],
    tables: Sequence[MdArgTable],
    rendered: dict[str, str],
    md_path: Path,
) -> list[Finding]:
    """Gating findings where a purely-numeric documented default clearly contradicts.

    Only checks an option whose default is documented *unambiguously*, in exactly
    one arguments table (see :func:`_option_default_cell`), so a subparser tool
    that repeats an option across subcommand tables is never falsely flagged.
    """
    findings: list[Finding] = []
    for option in options:
        if option.hidden or option.required or option.is_flag:
            continue
        actual = _option_default_number(option, rendered)
        checkable, cell = _option_default_cell(tables, option.name)
        if actual is None or not checkable or cell is None:
            continue
        documented = _cell_number(cell)
        if documented is None:
            continue  # a paraphrased default is not an unambiguous contradiction
        if not math.isclose(documented, actual, rel_tol=0.0, abs_tol=1e-9):
            findings.append(
                Finding(
                    CHECK_B,
                    SEVERITY_GATING,
                    str(md_path),
                    None,
                    option.name,
                    f"documented default {cell.strip()} for {option.name} does not "
                    f"match the actual default {_number_str(actual)}",
                    f"update the doc's default for {option.name} to {_number_str(actual)}",
                )
            )
    return findings


def _number_str(value: float) -> str:
    """Render a default number without a trailing ``.0`` when it is integral."""
    return str(int(value)) if value.is_integer() else str(value)


def _exit_code_findings(tree: ast.Module, md_text: str, md_path: Path) -> list[Finding]:
    """Advisory findings: an exit code the CLI returns but the doc does not document."""
    documented = parse_md_exit_codes(md_text)
    if documented is None:
        return []
    findings: list[Finding] = []
    for code in sorted(ast_exit_codes(tree) - documented):
        findings.append(
            Finding(
                CHECK_B,
                SEVERITY_ADVISORY,
                str(md_path),
                None,
                "exit codes",
                f"the CLI can return exit code {code}, but the doc's 'Exit codes' "
                "section does not document it",
                f"document exit code {code} in the doc",
            )
        )
    return findings


def _missing_doc_finding(
    py_path: Path, md_path: Path, *, allow_missing_doc: bool
) -> Finding:
    """The finding for a CLI whose companion doc is absent (gating unless allowed)."""
    severity = SEVERITY_ADVISORY if allow_missing_doc else SEVERITY_GATING
    return Finding(
        CHECK_B,
        severity,
        str(py_path),
        None,
        "companion .md",
        f"no companion doc {md_path.name} found beside the CLI (with a single-file "
        "--path, --doc names another doc); every argparse CLI must ship a companion "
        "doc (pass --allow-missing-doc to downgrade this to advisory)",
        f"add a companion {md_path.name} documenting the CLI, or name its doc with --doc",
    )


def _no_option_surface_finding(md_path: Path) -> Finding:
    """The gating finding for a doc with neither an arguments table nor a ``--help`` block.

    It gates because without either shape the
    coverage check verifies nothing, and an advisory saying so was easy to miss.
    """
    return Finding(
        CHECK_B,
        SEVERITY_GATING,
        str(md_path),
        None,
        "arguments table",
        "found no option surface: neither a CLI-arguments table (a Markdown table "
        "whose first header cell is Argument/Option/Flag) nor a pasted --help block "
        "(a fenced code block whose first line starts with 'usage:'); option "
        "coverage not verified",
        "add an arguments table, or paste the CLI's --help output in a fenced block",
    )


def _defaults_skipped_finding(md_path: Path) -> Finding:
    """The one advisory saying documented defaults went unchecked without run_help."""
    return Finding(
        CHECK_B,
        SEVERITY_ADVISORY,
        str(md_path),
        None,
        "defaults",
        "documented defaults were not checked: comparing them needs the CLI's live "
        "--help, and --run-help (or run_help = true in [tool.cli_doc_consistency]) "
        "executes the target to get it",
        "pass --run-help to compare documented defaults, if the target is trusted",
    )


def _render_failed_finding(md_path: Path, failure: RenderFailure | None) -> Finding:
    """The one advisory saying the requested live render failed, and what was used."""
    reason = failure.reason if failure is not None else "no reason given"
    return Finding(
        CHECK_B,
        SEVERITY_ADVISORY,
        str(md_path),
        None,
        "defaults",
        f"the live --help render failed ({reason}); documented defaults were "
        "compared against literal defaults in the source instead",
        "make '<python> <tool> --help' run cleanly from the tool's folder",
    )


def check_md_mirror(
    py_path: Path,
    md_path: Path,
    tree: ast.Module,
    md_text: str | None,
    *,
    render_help: RenderHelpFn,
    python: str,
    allow_missing_doc: bool = False,
    run_help: bool = False,
) -> list[Finding]:
    """Verify the companion doc mirrors the parser (conservative; see module docstring).

    A missing companion doc gates by default: a discovered argparse CLI must
    ship its doc. ``allow_missing_doc`` downgrades only that one finding to advisory;
    every other mirror check is unaffected.

    The doc's option surface is the union of its arguments tables and its pasted
    ``--help`` blocks. Option coverage is AST-only and always runs. Comparing
    documented defaults needs the live ``--help``, which executes the target, so it
    runs only with ``run_help``; without it, a doc that documents defaults gets one
    advisory saying they were not checked.
    """
    if md_text is None:
        return [
            _missing_doc_finding(py_path, md_path, allow_missing_doc=allow_missing_doc)
        ]
    options = cli_options_from_ast(tree)
    findings: list[Finding] = []
    surfaces = [
        *parse_md_arg_tables(md_text),
        *parse_md_help_blocks(md_text, argument_group_titles(tree)),
    ]
    if not surfaces:
        findings.append(_no_option_surface_finding(md_path))
    else:
        findings.extend(_coverage_findings(options, surfaces, md_path))
        if any(surface.has_default_column for surface in surfaces):
            if run_help:
                help_text = render_help(py_path, python)
                rendered: dict[str, str] = {}
                if isinstance(help_text, str):
                    rendered = parse_help_defaults(help_text)
                else:
                    findings.append(_render_failed_finding(md_path, help_text))
                findings.extend(_default_findings(options, surfaces, rendered, md_path))
            else:
                findings.append(_defaults_skipped_finding(md_path))
    findings.extend(_exit_code_findings(tree, md_text, md_path))
    return findings
