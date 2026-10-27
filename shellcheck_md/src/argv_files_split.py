#!/usr/bin/env python3
"""Split a ``shellcheck_md`` command line at ``--files``: options before, file names after.

Private to ``shellcheck_md`` -- it lives in that folder's ``src/``, and nothing outside this
tool folder may import it. The name is specific rather than a bare ``argv.py`` for the
reason ``fence_findings`` gives: a tool's ``src/`` goes on ``sys.path`` as a
directory, so a generic module name would collide in a process importing two tools.

Why the split exists: pre-commit runs a hook's command with the staged file names appended,
and this tool's hook command ends in ``--files``. Handed to argparse, those names are
parsed like anything else, so a staged file NAMED like an option -- ``--shellcheck=<path>``,
or any abbreviation argparse would accept -- became that option, and a file in the
repository chose which executable ran as the backend. Only what precedes the first
``--files`` is parsed; every token after it is a file name, ``--files`` and ``--root``
included.

Entry point assumptions
-----------------------
:func:`split_at_files` is pure; :func:`attach_files` writes only through the parser it is
handed, whose usage errors go to stderr and exit 2, as argparse's do. Parsing itself stays
in ``shellcheck_md.main``. Reached through the ``sys.path`` insert in ``shellcheck_md``.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

# The option that ends the parsed part of a command line, named once: the parser declares
# it for --help under this name, and the split looks for it under the same one.
FILES_FLAG = "--files"

# The ``--files=<name>`` spelling, which argparse would also have read as this option.
_FILES_EQUALS = f"{FILES_FLAG}="


def split_at_files(tokens: Sequence[str]) -> tuple[list[str], list[str] | None]:
    """Return (the tokens to parse as options, the file names) for *tokens*.

    The file names are every token after the first ``--files``, taken literally, or
    ``None`` when no ``--files`` is present. ``--files=<name>`` counts as the flag with
    *name* as the first file, since argparse would have read it that way too. An empty
    list means ``--files`` was given with nothing after it, which the caller refuses.
    """
    for index, token in enumerate(tokens):
        if token == FILES_FLAG:
            return list(tokens[:index]), list(tokens[index + 1 :])
        if token.startswith(_FILES_EQUALS):
            first = token[len(_FILES_EQUALS) :]
            return list(tokens[:index]), [first, *tokens[index + 1 :]]
    return list(tokens), None


def attach_files(
    parser: argparse.ArgumentParser, args: argparse.Namespace, names: list[str] | None
) -> None:
    """Set ``args.files`` from *names*, the split's file names, and refuse a bad mix.

    Called after *parser* has parsed the tokens before ``--files``. Each name becomes a
    ``Path`` as it is, never an option. ``--files`` with nothing after it, neither input,
    and both inputs are usage errors, which *parser* reports and exits 2 for.
    """
    if names == []:
        parser.error(f"argument {FILES_FLAG}: expected at least one argument")
    args.files = [Path(name) for name in names or ()]
    if args.root is None and not args.files:
        parser.error("give --root or --files")
    if args.root is not None and args.files:
        parser.error("give --root or --files, not both")
