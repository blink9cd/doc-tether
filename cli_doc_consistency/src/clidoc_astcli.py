"""The argparse surface of a CLI, read out of its AST and never executed.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

One concern, stated once: given a parsed module, what does its ``argparse`` surface look
like? That covers whether the module is a CLI at all, the ``add_argument`` calls it makes,
the literal defaults and ``help=`` text it passes, and the exit codes ``main`` returns.
The checks that *judge* those facts live elsewhere; this module only reports them.

It is not the only AST reader, and the boundary is worth naming because the docstrings
either side of it read true alone. ``clidoc_floor`` walks the same tree for the module
docstring, the parser epilog and the imports, and ``clidoc_astchecks`` re-walks the
``add_argument`` calls to judge them. What lands here is the reading that more than one
check needs; a walk with a single consumer settles into that consumer.

Reading rather than executing is the whole design. A gate that imported the tool under
test would run its module-level code, and a tool whose parser is built inside ``main``
would need that call made. So every fact here is derived statically, and anything that
cannot be proven from a literal is skipped rather than guessed. That is why a
non-literal first argument is not reported as positional, and a computed default is not
compared against the doc.

Entry point assumptions
-----------------------
Pure functions over an ``ast`` tree: no filesystem, no subprocesses, no streams, no
sibling libraries. Importing this module on its own needs no import order.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator

from clidoc_model import OptionInfo


# ---------------------------------------------------------------------------
# AST helpers.
# ---------------------------------------------------------------------------
#: What a parser constructor's name ends with: ``argparse.ArgumentParser`` itself, or a
#: subclass named for it such as ``stdio_utf8.EscapingArgumentParser``, which the stdio
#: library tells every tool to adopt. An exact match would drop each adopting tool from
#: this gate without a word.
_PARSER_NAME_SUFFIX = "ArgumentParser"


def names_argument_parser(func: ast.expr) -> bool:
    """True when a call's *func* names an argparse parser class, by attribute or bare name.

    Covers ``argparse.ArgumentParser(``, a bare ``ArgumentParser(`` (the
    ``from argparse import ArgumentParser`` style), and a subclass named for it.
    """
    if isinstance(func, ast.Attribute):
        return func.attr.endswith(_PARSER_NAME_SUFFIX)
    return isinstance(func, ast.Name) and func.id.endswith(_PARSER_NAME_SUFFIX)


def is_cli_module(tree: ast.Module) -> bool:
    """True when *tree* constructs an argparse parser (our CLI signal)."""
    return any(
        isinstance(node, ast.Call) and names_argument_parser(node.func)
        for node in ast.walk(tree)
    )


def _iter_add_argument_calls(tree: ast.Module) -> Iterator[ast.Call]:
    """Yield every ``<parser>.add_argument(...)`` call node in *tree*."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument"
        ):
            yield node


def _is_true_constant(node: ast.expr | None) -> bool:
    """True when *node* is the literal ``True`` (for ``required=True``)."""
    return isinstance(node, ast.Constant) and node.value is True


def _is_suppress(node: ast.expr | None) -> bool:
    """True when *node* is ``argparse.SUPPRESS`` / ``SUPPRESS`` (a hidden option)."""
    if isinstance(node, ast.Attribute):
        return node.attr == "SUPPRESS"
    return isinstance(node, ast.Name) and node.id == "SUPPRESS"


def _literal_value(node: ast.expr) -> tuple[bool, object]:
    """Return ``(is_literal, value)`` for a ``default=`` node we can read statically.

    Constants and all-constant lists are literals; anything else (a name, call,
    or f-string) is dynamic and returns ``(False, None)``.
    """
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.List) and all(
        isinstance(e, ast.Constant) for e in node.elts
    ):
        return True, [e.value for e in node.elts if isinstance(e, ast.Constant)]
    return False, None


def _action_string(keywords: dict[str, ast.expr]) -> str | None:
    """The ``action=`` string literal, or None when absent / non-literal."""
    node = keywords.get("action")
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


# The action argparse adds a "--no-<name>" string for, beside the option's own.
_NEGATABLE_ACTION = "BooleanOptionalAction"


def _is_negatable(node: ast.expr | None) -> bool:
    """True when ``action=`` names ``argparse.BooleanOptionalAction`` (or the bare name)."""
    if isinstance(node, ast.Attribute):
        return node.attr == _NEGATABLE_ACTION
    return isinstance(node, ast.Name) and node.id == _NEGATABLE_ACTION


def _long_aliases(longs: list[str], action: ast.expr | None) -> tuple[str, ...]:
    """Every long string an option answers to: its own, then ``--no-`` ones if negatable."""
    negated = [f"--no-{s[2:]}" for s in longs] if _is_negatable(action) else []
    return (*longs, *negated)


def cli_options_from_ast(tree: ast.Module) -> list[OptionInfo]:
    """Distill every long-option ``add_argument`` into an :class:`OptionInfo`.

    Positional and short-only arguments are skipped (Check B targets named long
    options); the auto-added ``--help`` is skipped too.
    """
    options: list[OptionInfo] = []
    for call in _iter_add_argument_calls(tree):
        strings = [
            a.value
            for a in call.args
            if isinstance(a, ast.Constant) and isinstance(a.value, str)
        ]
        longs = [s for s in strings if s.startswith("--")]
        if not longs or longs[0] == "--help":
            continue
        keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg}
        action = _action_string(keywords)
        has_lit, lit = (
            _literal_value(keywords["default"])
            if "default" in keywords
            else (False, None)
        )
        options.append(
            OptionInfo(
                name=longs[0],
                required=_is_true_constant(keywords.get("required")),
                is_flag=action in ("store_true", "store_false"),
                has_literal_default=has_lit,
                literal_default=lit,
                hidden=_is_suppress(keywords.get("help")),
                lineno=call.lineno,
                aliases=_long_aliases(longs, keywords.get("action")),
            )
        )
    return options


def _literal_group_title(call: ast.Call) -> str | None:
    """The title of an ``add_argument_group`` call when it is a string literal, else None.

    The title is the first positional argument or the ``title=`` keyword; a name, an
    f-string or any other expression cannot be known without running the code.
    """
    node = call.args[0] if call.args else None
    for keyword in call.keywords:
        if keyword.arg == "title":
            node = keyword.value
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def argument_group_titles(tree: ast.Module) -> set[str]:
    """Every literal argument-group title in *tree*, as written.

    argparse prints a custom group's options under ``<title>:``, so Check B needs these
    to read a pasted ``--help`` block's custom sections. Non-literal titles are skipped.
    """
    titles: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "add_argument_group"
        ):
            title = _literal_group_title(node)
            if title is not None:
                titles.add(title)
    return titles


def help_literal_text(node: ast.expr | None) -> str | None:
    """Return the literal text of a ``help=`` value, or None when non-literal.

    A plain string constant returns its value; an f-string returns the
    concatenation of its *literal* parts (the ``{...}`` substitutions are dropped,
    since Check A reasons about the literal text a human typed). A bare name, a
    call, or a concatenation expression returns None (skipped, not guessed).
    """
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        return "".join(
            v.value
            for v in node.values
            if isinstance(v, ast.Constant) and isinstance(v.value, str)
        )
    return None


def _is_sys_exit(func: ast.expr) -> bool:
    """True when *func* is ``sys.exit`` (an exit-code source we can read)."""
    return (
        isinstance(func, ast.Attribute)
        and func.attr == "exit"
        and isinstance(func.value, ast.Name)
        and func.value.id == "sys"
    )


def _int_constants(node: ast.AST) -> set[int]:
    """Every non-bool integer constant anywhere within *node*."""
    return {
        sub.value
        for sub in ast.walk(node)
        if isinstance(sub, ast.Constant)
        and isinstance(sub.value, int)
        and not isinstance(sub.value, bool)
    }


def ast_exit_codes(tree: ast.Module) -> set[int]:
    """The integer exit codes ``main`` returns plus any ``sys.exit(<int>)`` literal."""
    codes: set[int] = set()
    main = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "main"
        ),
        None,
    )
    if main is not None:
        for sub in ast.walk(main):
            if isinstance(sub, ast.Return) and sub.value is not None:
                codes |= _int_constants(sub.value)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_sys_exit(node.func):
            for arg in node.args:
                codes |= _int_constants(arg)
    return codes
