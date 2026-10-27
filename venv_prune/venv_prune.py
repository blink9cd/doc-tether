#!/usr/bin/env python3
"""Answer "is this directory a Python virtual environment?" from the PEP 405 marker.

Why this exists
---------------
A discovery-based gate prunes a set of directory NAMES it should never descend
into -- ``.git``, ``node_modules``, ``__pycache__``, the tool caches, and ``.venv``.
A name list only knows the names someone thought of. A virtualenv created under any
other name, at the root a user then points the gate at, turns into thousands of
findings about third-party package code. Nothing leaked and nothing is misconfigured
-- the walk simply descended into a tree that was never ours to check.

Widening the name list would fix that one name and leave the class open, because the
name is a *proxy*. The authoritative signal is the one the runtime itself uses:
PEP 405 defines a virtual environment as a directory containing ``pyvenv.cfg``, and
CPython computes ``sys.prefix`` by finding exactly that file. Keying on the marker
makes the answer independent of what anyone named the directory, so a hand-made
``venv/``, a versioned ``.venv311/``, and a tool's own ``.tool-env`` all prune for
the same reason: state is read from its authoritative source, not from a proxy.

This is ADDITIVE to each tool's existing name set, not a replacement for it. A
half-built or partially-deleted venv whose ``pyvenv.cfg`` never got written still
should not be walked, and the name list is what catches that.

Why it does not parse the marker
--------------------------------
Presence is the contract. PEP 405 mandates no particular key, tools disagree about
what they write into it, and a stricter check could only ever produce false negatives
-- a real venv the gate then walks. The file's *existence* is what the runtime keys
on, so it is what this keys on too.

Entry point assumptions
-----------------------
Called once per directory at a walk's prune step, alongside the caller's own name
check, on the directory itself rather than on its children -- so a venv root is never
entered and nothing beneath it is ever visited. The function is a pure query: it
reads one path and writes nothing.

A path that cannot be examined -- missing, permission-denied, or not a directory at
all -- reads as *not* a virtualenv, which is the safe direction for a GATE: the cost
of a wrong ``False`` is scanning a tree that turns out to be third-party, while the
cost of a wrong ``True`` is silently skipping real source and reporting a clean run
for code nobody checked. ``Path.is_file`` already collapses those OSError cases to
``False``, so the safe default is the default.
"""

from __future__ import annotations

from pathlib import Path

# PEP 405's marker file. A directory holding one IS a virtual environment root -- this
# is the same file CPython looks for when it computes sys.prefix at startup.
_MARKER_NAME = "pyvenv.cfg"


def is_virtualenv(path: Path) -> bool:
    """True when *path* is the root directory of a Python virtual environment.

    Judged solely by the presence of a ``pyvenv.cfg`` *file* directly inside *path*
    (PEP 405), so the verdict does not depend on the directory's name. Returns
    ``False`` -- never raises -- for a file, a missing path, or a directory that
    cannot be read.
    """
    return (path / _MARKER_NAME).is_file()


def is_under_virtualenv(path: Path, root: Path) -> bool:
    """True when *path* sits inside a virtualenv somewhere below *root*.

    The ancestor form of :func:`is_virtualenv`, for the gates that filter an
    already-discovered path (``rglob`` + a component test) instead of pruning
    directories during a walk. Those cannot prune, so they must ask about the whole
    chain.

    *root* is exclusive and bounds the search: a marker on *root* itself is ignored,
    because the caller deliberately chose to scan it and one stray file must not blank
    the entire run. Stopping there also keeps the walk from escaping upward into
    directories the caller never asked about.
    """
    for parent in path.parents:
        if parent == root:
            return False
        if is_virtualenv(parent):
            return True
    return False
