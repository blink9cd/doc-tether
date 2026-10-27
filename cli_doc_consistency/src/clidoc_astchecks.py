"""The two checks that need nothing but the AST: hardcoded defaults, and positionals.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

Checks A and D share this module because they share a shape, not merely a size: each
walks the same ``add_argument`` calls, judges one literal, and emits a gating finding with
no other input -- no companion doc, no rendered help, no imports. That is what separates
them from checks B and C, which each need a second artifact to compare against and get a
module apiece. A reader arriving at "why was my ``add_argument`` flagged?" arrives holding
both possibilities at once.

Entry point assumptions
-----------------------
Pure functions over an ``ast`` tree plus the path used for reporting: no filesystem reads,
no subprocesses, no streams, no sibling libraries. Importing this module on its own needs
no import order.
"""

from __future__ import annotations

import ast
from pathlib import Path

from clidoc_astcli import _iter_add_argument_calls, help_literal_text
from clidoc_model import CHECK_A, CHECK_D, SEVERITY_GATING, Finding


# --- Check A / Check C literals ---------------------------------------------
# The token argparse's ArgumentDefaultsHelpFormatter looks for to decide it must
# NOT append its own default; when present, a literal "(default" is intentional.
_DEFAULT_MARKER = "(default"


_PERCENT_DEFAULT_TOKEN = "%(default)"


# ---------------------------------------------------------------------------
# Check A: hardcoded default in a help= literal.
# ---------------------------------------------------------------------------
def check_hardcoded_defaults(tree: ast.Module, py_path: Path) -> list[Finding]:
    """Flag any ``help=`` literal that hardcodes ``(default...)`` without ``%(default)``."""
    findings: list[Finding] = []
    for call in _iter_add_argument_calls(tree):
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        text = help_literal_text(keywords.get("help"))
        if text is None:
            continue
        low = text.lower()
        if _DEFAULT_MARKER in low and _PERCENT_DEFAULT_TOKEN not in low:
            strings = [
                a.value
                for a in call.args
                if isinstance(a, ast.Constant) and isinstance(a.value, str)
            ]
            name = next((s for s in strings if s.startswith("-")), "<arg>")
            findings.append(
                Finding(
                    check=CHECK_A,
                    severity=SEVERITY_GATING,
                    file=str(py_path),
                    line=call.lineno,
                    context=name,
                    message=(
                        "help text hardcodes '(default...)' but has no %(default) token; "
                        "ArgumentDefaultsHelpFormatter appends its own '(default: X)', "
                        "so the default double-prints and can drift"
                    ),
                    fix=(
                        "drop the literal '(default: ...)' and let the formatter render it, "
                        "or reference it with the %(default)s placeholder"
                    ),
                )
            )
    return findings


# ---------------------------------------------------------------------------
# Check D: no positional CLI arguments (named options only). Opt-in: the caller runs it
# only when --check-positionals or check_positionals = true asks, because it is a policy
# about how a CLI should look, not a doc-vs-code drift.
# ---------------------------------------------------------------------------
def check_positional_arguments(tree: ast.Module, py_path: Path) -> list[Finding]:
    """Flag every ``add_argument`` that defines a positional argument.

    A positional is an ``add_argument`` whose *first* argument is a string literal
    that does not start with ``-`` (``add_argument("value")``); an option string
    (``--value`` / ``-v``) starts with ``-`` and is the required form. A call with
    no positional args (``add_argument(dest=...)``) or whose first argument is a
    non-literal (a name/expr) cannot be statically proven positional, so it is
    skipped rather than guessed -- Check B / Check A stay unaffected.
    """
    findings: list[Finding] = []
    for call in _iter_add_argument_calls(tree):
        if not call.args:
            continue  # keyword-only add_argument(...) -> no option string to inspect
        first = call.args[0]
        if not isinstance(first, ast.Constant) or not isinstance(first.value, str):
            continue  # non-literal / non-string first arg -> cannot prove positional
        if first.value.startswith("-"):
            continue  # an option string (--flag / -v): already a named option
        findings.append(
            Finding(
                check=CHECK_D,
                severity=SEVERITY_GATING,
                file=str(py_path),
                line=call.lineno,
                context=first.value,
                message=(
                    f"'{first.value}' is a positional CLI argument; user-facing "
                    "arguments should be named options, which are self-documenting "
                    "and order-independent"
                ),
                fix=(
                    f"make {first.value} a named option (e.g. --{first.value}), "
                    "not a positional argument"
                ),
            )
        )
    return findings
