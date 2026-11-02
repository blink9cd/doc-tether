"""Check C: the Python floor a module states, against the floor its imports imply.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

The check is a comparison between two readings of one module, and both readings live here
because neither is useful alone. The implied floor comes from the imports: a stdlib module
that only exists from a given version raises the floor, unless a backport of it is also
imported. The stated floors come from the prose (the module docstring, the parser's
epilog, and the companion ``.md``), each of which a human wrote and any of which can go
stale independently, which is why every stale location is reported rather than just the
first.

Reading the epilog needs the parser construction found in the AST, so the two
``ArgumentParser``-shaped predicates sit here rather than in ``clidoc_astcli``: their only
consumer is the epilog walk, and a closed cluster settles into the module that owns it.

Entry point assumptions
-----------------------
Pure functions over an ``ast`` tree and already-read text: no filesystem reads, no
subprocesses, no streams, no sibling libraries. Importing this module on its own needs no
import order.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Sequence
from pathlib import Path

from clidoc_astcli import names_argument_parser
from clidoc_model import CHECK_C, SEVERITY_GATING, Claim, Finding


# stdlib modules that only exist from a given Python; importing one raises the
# effective floor. "tomli" is the PyPI backport of tomllib, not a stdlib module,
# so it is deliberately absent here and instead recorded as tomllib's backport.
VERSION_GATED_STDLIB: dict[str, tuple[int, int]] = {
    "tomllib": (3, 11),
    "zoneinfo": (3, 9),
    "graphlib": (3, 9),
}


# A gated module whose backport is also imported does not raise the floor (the
# code supports the older runtime via the backport).
_BACKPORTS: dict[str, str] = {"tomllib": "tomli"}


# A stated version floor: "3.11+", "3.9+", etc. Higher-than-required mentions
# (e.g. "the 3.13+ gate") are captured too but never fail (stated >= implied).
_FLOOR_RE = re.compile(r"3\.(\d+)\+")


# ---------------------------------------------------------------------------
# Check C: stated floor vs version-gated imports.
# ---------------------------------------------------------------------------
def _top_module(name: str) -> str:
    """The first dotted component of a module name (``a.b.c`` -> ``a``)."""
    return name.split(".")[0]


def collect_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Return ``(hard, all)`` import module names.

    ``hard`` is the set imported *unconditionally at module scope* (directly in
    the module body). Only these raise the floor, so a lazy or ``try/except``
    guarded import never causes a false positive. ``all`` is every import
    anywhere, used to spot a backport (e.g. ``tomli``) that neutralizes a gate.
    """
    hard: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            hard.update(_top_module(alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            hard.add(_top_module(node.module))
    every: set[str] = set()
    for walked in ast.walk(tree):
        if isinstance(walked, ast.Import):
            every.update(_top_module(alias.name) for alias in walked.names)
        elif isinstance(walked, ast.ImportFrom) and walked.module:
            every.add(_top_module(walked.module))
    return hard, every


def implied_floor(hard: set[str], every: set[str]) -> tuple[int, int] | None:
    """The minimum Python the *unconditional* imports require, or None."""
    floors: list[tuple[int, int]] = []
    for name in hard:
        if name not in VERSION_GATED_STDLIB:
            continue
        backport = _BACKPORTS.get(name)
        if backport is not None and backport in every:
            continue  # the backport covers the older runtime
        floors.append(VERSION_GATED_STDLIB[name])
    return max(floors) if floors else None


def _version_str(version: tuple[int, int]) -> str:
    """Render a ``(3, 11)`` version tuple as ``"3.11"``."""
    return f"{version[0]}.{version[1]}"


def _claims_in_text(text: str, base_lineno: int) -> list[Claim]:
    """Every ``3.x+`` claim in *text*, with line numbers offset from *base_lineno*."""
    claims: list[Claim] = []
    for offset, line in enumerate(text.splitlines()):
        for match in _FLOOR_RE.finditer(line):
            claims.append(
                (base_lineno + offset, (3, int(match.group(1))), line.strip())
            )
    return claims


def stated_floors(text: str) -> list[Claim]:
    """Every ``3.x+`` floor claim in raw *text* (for the .md, which has no comments)."""
    return _claims_in_text(text, 1)


def _leading_docstring(body: Sequence[ast.stmt]) -> tuple[str, int] | None:
    """The ``(text, line)`` of a leading docstring in *body*, or None."""
    if body and isinstance(body[0], ast.Expr):
        value = body[0].value
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            return value.value, value.lineno
    return None


def _is_formatter_class(node: ast.ClassDef) -> bool:
    """True when *node* is a help-formatter class (name or a base mentions Formatter)."""
    if "Formatter" in node.name:
        return True
    for base in node.bases:
        name = base.attr if isinstance(base, ast.Attribute) else getattr(base, "id", "")
        if "Formatter" in name:
            return True
    return False


def _epilog_node(tree: ast.Module) -> ast.expr | None:
    """The ``epilog=`` value node of the first ArgumentParser construction, or None."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and names_argument_parser(node.func):
            for keyword in node.keywords:
                if keyword.arg == "epilog":
                    return keyword.value
    return None


def _epilog_text(tree: ast.Module) -> tuple[str, int] | None:
    """The ``(text, line)`` of a Constant-string ``epilog=``, or None."""
    epilog = _epilog_node(tree)
    if isinstance(epilog, ast.Constant) and isinstance(epilog.value, str):
        return epilog.value, epilog.lineno
    return None


def _floor_claim_texts(tree: ast.Module) -> list[tuple[str, int]]:
    """``(text, base-line)`` for each *canonical* floor-claim location.

    Only the module docstring, any help-formatter class docstring, and the
    argparse ``epilog`` state a deployment floor.
    Restricting to these deliberately ignores a floor mentioned illustratively in
    some other comment, help string, or function docstring: that is not a claim
    about *this* tool's supported runtime.
    """
    texts: list[tuple[str, int]] = []
    module_doc = _leading_docstring(tree.body)
    if module_doc is not None:
        texts.append(module_doc)
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_formatter_class(node):
            class_doc = _leading_docstring(node.body)
            if class_doc is not None:
                texts.append(class_doc)
    epilog = _epilog_text(tree)
    if epilog is not None:
        texts.append(epilog)
    return texts


def stated_floors_in_tree(tree: ast.Module) -> list[Claim]:
    """Every ``3.x+`` floor claim in *tree*'s canonical floor-claim string literals."""
    claims: list[Claim] = []
    for text, base_lineno in _floor_claim_texts(tree):
        claims.extend(_claims_in_text(text, base_lineno))
    return claims


def _claim_findings(
    claims: Sequence[Claim], target_file: Path, implied: tuple[int, int]
) -> list[Finding]:
    """Emit a Check-C finding for each claim in *claims* below *implied*."""
    findings: list[Finding] = []
    for lineno, version, _snippet in claims:
        if version < implied:
            findings.append(
                Finding(
                    check=CHECK_C,
                    severity=SEVERITY_GATING,
                    file=str(target_file),
                    line=lineno,
                    context=f"stated floor {_version_str(version)}+",
                    message=(
                        f"states Python {_version_str(version)}+ but the module's "
                        f"imports require at least {_version_str(implied)}"
                    ),
                    fix=f"raise the stated floor to Python {_version_str(implied)}+",
                )
            )
    return findings


def check_floor(
    tree: ast.Module,
    py_path: Path,
    md_path: Path,
    md_text: str | None,
) -> list[Finding]:
    """Flag every stated floor (in the .py and its .md) below the import-implied one."""
    hard, every = collect_imports(tree)
    implied = implied_floor(hard, every)
    if implied is None:
        return []
    findings = _claim_findings(stated_floors_in_tree(tree), py_path, implied)
    if md_text is not None:
        findings.extend(_claim_findings(stated_floors(md_text), md_path, implied))
    return findings
