#!/usr/bin/env python3
"""Say so when a gate examined nothing because its excludes hid every candidate.

Why this exists
---------------
A discovery-based gate that finds no work prints a zero and passes: ``Checked 0
file(s): 0 finding(s).`` That reads as verified-clean, and it is indistinguishable from
the case where the effective exclude set pruned *every* candidate under the target: a
gate that examined nothing at all. Both render ``PASS``. So an ``exclude`` entry broader
than its author meant reports a clean gate, for a tree nobody is checking, indefinitely,
and the broader the entry, the quieter the failure.

The two zeros are not the same claim. "There were no YAML files here" is a fact about the
tree; "every YAML file here was excluded" is a fact about the configuration, and only the
second one can be a mistake. This module exists so every gate that uses it draws that
distinction with the same words, rather than each one inventing its own phrasing (or
omitting it).

Why it does not fail the build
-----------------------------
The verdict is deliberately left alone. Every standing exclusion in a mature tree is
intentional: a ``samples/`` fixture tree holding a deliberate defect for
hand-verification, a vendored third-party tree that is not the project's to lint. Failing
on those would break every build that runs the check and teach the reader to ignore it. The
line is for the case where the exclusion is *not* intentional, and it addresses that by
being legible, not by being fatal.

Entry point assumptions
-----------------------
``hidden_count`` is called once, immediately after the tool's own discovery, and its
result is handed to ``note``. The returned line (when there is one) is printed above the
tool's own count line, so the caveat precedes the number it qualifies. Both functions are
pure (no I/O, no filesystem access, no knowledge of any particular gate), so the tool
keeps sole ownership of how it discovers and prunes; this module owns only the rule for
*when* to speak and the words to use.
"""

from __future__ import annotations

from collections.abc import Callable

# Sentinel for "nothing was hidden", returned as a count so callers can pass it straight
# to note() without a branch of their own.
_NOTHING_HIDDEN = 0


def hidden_count(examined: int, count_all: Callable[[], int]) -> int:
    """How many candidates the excludes hid, or 0 when anything was examined.

    *count_all* must re-run the caller's own discovery with its configured excludes off
    (the ones :func:`note` blames) while keeping any prune that reflects what was never a
    candidate for those to hide. A wholly empty exclude set is the wrong baseline wherever
    a gate prunes on some other authority's answer: it counts those paths as concealed and
    pins them on a config table that never touched them. Passing the real discovery
    function (rather than a reimplemented walk) is the point: the count then cannot drift
    from the prune rules it is reporting on.

    It is called only when *examined* is zero, so the common path pays nothing and a tool
    that examined work never walks its tree twice.
    """
    if examined:
        return _NOTHING_HIDDEN
    return count_all()


def note(hidden: int, *, noun: str, table: str, flag: str = "--path") -> str | None:
    """The ``NOTHING EXAMINED`` line for *hidden* pruned candidates, else ``None``.

    *noun* is the singular unit the gate counts ("file", "doc", "unit"); *table* is the
    tool's ``[tool.<name>]`` config table; *flag* is the tool's own target option, so the
    line names the argument the reader actually passed (``--root`` for ``shellcheck_md``,
    or ``--folder`` for a tool that takes that).

    Returns ``None``, not an empty string, when nothing was hidden, so a caller
    appending to a line list can filter falsy values without emitting a blank line.
    """
    if not hidden:
        return None
    return (
        f"NOTHING EXAMINED: all {hidden} candidate {noun}(s) under {flag} were pruned by "
        f"the exclude set ([tool.{table}] exclude and/or --exclude), so this result "
        "verifies nothing. Intentional for a deliberately-excluded tree; a surprise "
        "means the exclude is broader than intended."
    )
