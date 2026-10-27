"""The fixtures cli_doc_consistency's grouped suites are built on.

Shared by composition -- imported, never subclassed. They live here rather than
inside one suite because a fixture imported ACROSS suites is what makes the
decomposition honest: the alternative is near-copies that drift, and a drifted copy
fails in whichever suite touches it first, pointing at the wrong subject.

Every helper is PUBLIC. A leading underscore would claim these are private to one
module, and more than one suite imports them.
"""

from __future__ import annotations

import ast
import textwrap


def parse_module(source: str) -> ast.Module:
    """Parse *source* (dedented) into a module AST."""
    return ast.parse(textwrap.dedent(source))


def first_expr_value(source: str) -> ast.expr:
    """The value of the first expression statement in *source* (mypy-narrowed)."""
    node = parse_module(source).body[0]
    assert isinstance(node, ast.Expr)
    return node.value


def first_call(source: str) -> ast.Call:
    """The first call expression in *source* (mypy-narrowed)."""
    value = first_expr_value(source)
    assert isinstance(value, ast.Call)
    return value
