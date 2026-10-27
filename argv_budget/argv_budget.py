#!/usr/bin/env python3
"""Split a path list across command lines that each fit the OS argv ceiling.

Why this exists
---------------
A tool that hands a discovered file set to a backend puts those paths **on the command
line**, and a command line has a hard ceiling: 32,767 characters for ``CreateProcess``,
and only 8,191 when what is launched is a ``.CMD``/``.BAT`` shim -- because CreateProcess
wraps such a shim as ``%COMSPEC% /c "<argv>"``, so cmd.exe, not CreateProcess, parses the
line. Every tool that hands a discovered file set to a backend reaches that ceiling sooner
or later; this module is the one splitter they can share.

Overflowing is worse than it sounds, which is why splitting is not optional. The OS
reports ``WinError 206``, Python raises it as ``FileNotFoundError``, and a caller that
maps that onto "the executable is missing" sends its reader after a PATH problem that does
not exist. A ``.CMD`` shim is nastier still: cmd.exe prints ``The command line is too
long.`` and exits 1 -- the same code a linter uses for "findings reported".

The budget applies on every platform rather than behind a Windows branch. POSIX
``ARG_MAX`` is far larger, so a per-OS budget would exercise this path only where it is
hardest to test, and a green run there would prove nothing about the tightest target.

What is deliberately NOT here
-----------------------------
Each backend's own argv **prefix** (which subcommand, which flags), how it wants a path
**rendered** (a bare string, or markdownlint-cli2's ``:``-prefixed literal form), and what
its exit codes **mean**. Those are per-backend facts that belong with the backend; only
the measuring and the splitting are shared.

Entry point assumptions
-----------------------
``batch_paths`` is called once, after discovery and after the executable has been
RESOLVED: :func:`argv_limit_for` judges by file suffix, so a bare name like
``markdownlint-cli2`` carries nothing to judge and would silently take the looser ceiling.

This module is a library: it has no CLI, and is imported by its sibling tool folders via
the ``sys.path.insert`` pattern documented in ``argv_budget.md``.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

#: cmd.exe's own command-line ceiling -- what binds for a ``.CMD``/``.BAT`` shim, since
#: CreateProcess wraps one as ``%COMSPEC% /c "<argv>"`` and cmd.exe parses the result.
CMD_SHIM_ARGV_LIMIT = 8191
#: CreateProcess's ceiling -- what binds for a real executable, launched directly.
CREATE_PROCESS_ARGV_LIMIT = 32767
#: Suffixes whose execution is brokered by cmd.exe, and so bound by its tighter limit.
_SHIM_SUFFIXES: frozenset[str] = frozenset({".cmd", ".bat"})
#: Default reserve held out of the ceiling for the ``%COMSPEC% /c "..."`` wrapper, whose
#: exact rendering is not visible from here -- budgeting to the byte would overflow by
#: however long COMSPEC happens to be. A caller reserving for a wrapper it cannot measure
#: at all passes a larger ``headroom`` (2048, say).
DEFAULT_HEADROOM = 512


def rendered_length(args: Sequence[str]) -> int:
    """Length of *args* rendered as one command line, its quoting included.

    ``list2cmdline`` is what the subprocess layer actually builds, so a path carrying a
    space is measured WITH the quotes it will acquire. A raw character count under-reports
    every such token, letting a batch overflow the very ceiling it was sized against.
    """
    return len(subprocess.list2cmdline(args))


def argv_limit_for(executable: str) -> int:
    """The command-line ceiling that binds when running *executable*.

    A ``.CMD``/``.BAT`` shim is executed via ``%COMSPEC% /c``, so cmd.exe parses the line
    and its 8191-character limit applies; a real executable is launched by CreateProcess
    directly and gets 32767. Derived from the *resolved* path -- a bare name carries no
    suffix to judge by, so resolve before asking.
    """
    if Path(executable).suffix.lower() in _SHIM_SUFFIXES:
        return CMD_SHIM_ARGV_LIMIT
    return CREATE_PROCESS_ARGV_LIMIT


def batch_paths(
    paths: Sequence[Path],
    *,
    prefix_chars: int,
    limit: int,
    headroom: int = DEFAULT_HEADROOM,
    render: Callable[[Path], str] = str,
) -> list[list[Path]]:
    """Split *paths* into batches whose rendered argv stays under *limit*.

    *prefix_chars* is the rendered width of the fixed argv head -- executable, subcommand
    and flags -- which every batch pays for again. *headroom* is held back out of *limit*
    for a wrapper the caller does not build. *render* turns one path into the exact token
    the backend will receive, so the measurement matches what is actually emitted.

    ``list2cmdline`` quotes each argument independently and joins with single spaces, so a
    batch's width is exactly the prefix plus, per path, that token's rendered length plus
    one joining space -- which makes the running total exact rather than an approximation.

    A single path too wide to fit even alone still gets its own batch rather than being
    dropped: a gate that silently skips a file reports a clean run it never earned, which
    is the one outcome worse than failing on it.
    """
    budget = limit - headroom - prefix_chars
    batches: list[list[Path]] = []
    current: list[Path] = []
    used = 0
    for path in paths:
        cost = rendered_length([render(path)]) + 1  # +1 for the joining space
        if current and used + cost > budget:
            batches.append(current)
            current, used = [], 0
        current.append(path)
        used += cost
    if current:
        batches.append(current)
    return batches
