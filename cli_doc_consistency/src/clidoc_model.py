"""The vocabulary every other piece of this gate shares: check ids, severities, records.

Private to ``cli_doc_consistency``: it lives in that folder's ``src/``, so
nothing outside this tool folder may import it. Every module here carries the tool's
prefix: a ``src/`` dir placed on ``mypy_path`` contributes its module names to one flat
namespace shared with every other registered ``src/``, and two files of the same name do
not collide loudly: one silently shadows the other and is never type-checked. A bare
``model.py`` is exactly the name a second tool would reach for.

This module imports none of its siblings, which is what keeps the dependency graph
acyclic: every other module here may import this one. A constant used by a single module
lives with that module instead, so this does not become a dumping ground.

``RenderHelpFn`` lives here rather than beside the ``_render_help`` implementation it
describes, and that placement is load-bearing. The type is read by the two functions that
*receive* the render seam as a parameter, both of which live in sibling modules; keeping
the alias next to its implementation in the parent would make those siblings import the
parent and close a cycle. The implementation stays in the parent because the suite pins
it there.

Entry point assumptions
-----------------------
Definitions only: no I/O, no subprocesses, no streams, no sibling libraries. Importing
this module on its own needs no import order.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


# --- Check identifiers and severities ---------------------------------------
CHECK_A = "A"  # hardcoded-default in help=


CHECK_B = "B"  # .md-mirrors-CLI


CHECK_C = "C"  # stated-floor-vs-imports


CHECK_D = "D"  # positional (non-named) CLI argument


SEVERITY_GATING = "gating"  # drives exit 1


SEVERITY_ADVISORY = "advisory"  # reported, never gates


class TargetError(Exception):
    """Exit-2-class failure: a directly-named --path could not be read/parsed."""


@dataclass(frozen=True)
class Finding:
    """One drift finding: which check, how severe, where, and how to fix it."""

    check: str
    severity: str
    file: str
    line: int | None
    context: str
    message: str
    fix: str


@dataclass(frozen=True)
class OptionInfo:
    """One ``add_argument`` option, distilled from the AST (never executed)."""

    name: str  # the long option, e.g. "--path"
    required: bool
    is_flag: bool  # store_true / store_false
    has_literal_default: bool
    literal_default: object
    hidden: bool  # help=argparse.SUPPRESS
    lineno: int
    # Every long string the option answers to, ``name`` first: its other spellings
    # (``--dryrun`` beside ``--dry-run``) and, for a BooleanOptionalAction, ``--no-<name>``.
    # Empty means "only name", the default for an OptionInfo built by hand.
    aliases: tuple[str, ...] = ()

    @property
    def names(self) -> tuple[str, ...]:
        """Every long spelling of the option, ``name`` first."""
        return self.aliases or (self.name,)


@dataclass(frozen=True)
class MdArgTable:
    """One parsed option surface of a doc: an arguments table, or a pasted --help block.

    A subparser tool documents its options across several per-subcommand tables,
    so a doc yields a *list* of these (see :func:`parse_md_arg_tables`). A pasted
    ``--help`` block reads into the same shape (:func:`parse_md_help_blocks`), with
    ``has_default_column`` true when it renders any ``(default: X)``, so Check B treats
    the two alike.
    """

    options: dict[str, str | None]  # long option -> default-cell text (or None)
    has_default_column: bool


Claim = tuple[int, tuple[int, int], str]  # (line, version, stripped-line)


# ---------------------------------------------------------------------------
# Check B: the companion .md mirrors the CLI.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RenderFailure:
    """Why a requested live ``--help`` render produced no text (ASCII, one line)."""

    reason: str


# Runner seam: (tool_path, python) -> rendered --help text, or a RenderFailure (None
# when a fake has no reason to give) if it could not be run. Injected as a fake in
# tests; the real one runs a subprocess.
RenderHelpFn = Callable[[Path, str], str | RenderFailure | None]
