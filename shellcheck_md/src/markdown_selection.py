#!/usr/bin/env python3
"""Which Markdown files a ``shellcheck_md`` run examines, and which excludes bind them.

Private to ``shellcheck_md``: it lives in that folder's ``src/``, and nothing outside this
tool folder may import it. The name is specific rather than a bare ``selection.py``
for the reason ``fence_findings`` gives: a tool's ``src/`` goes on ``sys.path``
as a directory, so a generic module name would collide in a process importing two tools.

Two inputs reach a run. ``--root`` discovers files by glob, pruning caches, VCS trees and
virtualenvs. Files named on the command line, as a pre-commit hook passes its staged files,
are taken as named. Both answer to the same two exclude sources: ``--exclude`` and the
``[tool.shellcheck_md] exclude`` list in the nearest ``pyproject.toml`` holding that table,
for each file. It also finds the ShellCheck rc file nearest each Markdown file, which the
run copies beside the scripts lifted from it.

Entry point assumptions
-----------------------
Reached through the ``sys.path`` insert in ``shellcheck_md``, which also puts ``venv_prune``
on the path before this module is imported. Reads the filesystem, pyproject files and the
Markdown files themselves (:func:`read_shell_blocks`); starts no process and writes no
stream.
"""

from __future__ import annotations

import tomllib
from collections.abc import Sequence
from pathlib import Path

import venv_prune
from fence_findings import ShellBlock, extract_blocks

_CONFIG_TABLE = "shellcheck_md"  # the pyproject [tool.<name>] table this tool reads

# The rc-file names ShellCheck itself searches a script's directory and its parents for,
# in the order it tries them within one directory (its manual, "RC FILES"; the undotted
# name exists for sandboxes that hide dotfiles).
RCFILE_NAMES: tuple[str, ...] = (".shellcheckrc", "shellcheckrc")

# Directories never descended when discovering Markdown: a node_modules / cache / VCS tree
# is not ours to lint, and a node project's dependency READMEs would otherwise flood the
# run with SC findings.
_DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {".git", "node_modules", "__pycache__", ".mypy_cache", ".ruff_cache", ".venv"}
)


class ConfigError(Exception):
    """Exit-2-class failure: the tool's pyproject config could not be trusted."""


class UnreadableMarkdown(Exception):
    """A Markdown file could not be read as UTF-8 text: an exit-2 condition, never 1."""


def read_config_excludes(start: Path, *, stop: Path | None = None) -> tuple[Path, ...]:
    """Resolved ``[tool.shellcheck_md] exclude`` paths from the nearest configuring pyproject.

    Walks *start* and its ancestors for a ``pyproject.toml`` that has a
    ``[tool.shellcheck_md]`` table, and reads its ``exclude`` (paths relative to that
    file's directory). A pyproject without the table is passed over, as ruff passes over
    one without ``[tool.ruff]``: a sub-project's unrelated pyproject must not cancel the
    excludes above it. No configuring pyproject, or a table with no ``exclude``, is
    ``()``. A malformed file, a table that is not a table, or a non-list / non-string
    ``exclude`` raises :class:`ConfigError`: config we cannot trust must stop the gate,
    not silently shrink it. A malformed file stops the walk even without the table, since
    whether it has one cannot be known. *stop* (inclusive, when it is an ancestor) bounds
    the walk so tests do not climb into a real pyproject.
    """
    here = start.resolve()
    chain: tuple[Path, ...] = (here, *here.parents)
    if stop is not None:
        ceiling = stop.resolve()
        if ceiling in chain:
            chain = chain[: chain.index(ceiling) + 1]
    for candidate in chain:
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        try:
            data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError, OSError) as exc:
            raise ConfigError(f"cannot read or parse {pyproject}: {exc}") from exc
        tool = data.get("tool")
        table = tool.get(_CONFIG_TABLE) if isinstance(tool, dict) else None
        if table is None:
            continue
        if not isinstance(table, dict):
            raise ConfigError(f"[tool.{_CONFIG_TABLE}] in {pyproject} must be a table")
        raw = table.get("exclude")
        if raw is None:
            return ()
        if not isinstance(raw, list) or any(not isinstance(e, str) for e in raw):
            raise ConfigError(
                f"[tool.{_CONFIG_TABLE}] exclude in {pyproject} must be a list of strings"
            )
        return tuple((candidate / entry).resolve() for entry in raw)
    return ()


def should_exclude(path: Path, excludes: Sequence[Path]) -> bool:
    """Return True if *path* equals, or lives under, any resolved --exclude path."""
    resolved = path.resolve()
    for exc in excludes:
        if resolved == exc or exc in resolved.parents:
            return True
    return False


def _under_pruned_dir(path: Path, root: Path) -> bool:
    """Return True if a component of *path* below *root* is a default-pruned directory.

    Prunes node_modules / caches / VCS by name so a node project's dependency
    READMEs (and cache trees) are never lifted for shellcheck. Only the parts below
    *root* are judged: a root that itself sits inside a directory with a pruned name
    was chosen by the caller, and judging its own components pruned every file in it.
    """
    return any(part in _DEFAULT_EXCLUDE_DIRS for part in path.relative_to(root).parts)


def discover_markdown_files(
    root: Path, glob: str, excludes: Sequence[Path]
) -> list[Path]:
    """Return sorted Markdown files under *root* matching *glob*.

    A match that is not a regular file (a directory named ``notes.md``) is skipped, as
    is a file under a default-pruned directory (node_modules / caches / VCS), under a
    virtualenv, or under any *excludes* path. The virtualenv test is by the PEP 405
    marker rather than by name, and has to ask about the whole ancestor chain: this tool
    globs and then filters, so it has no directory-prune step at which to stop
    descending.
    """
    return sorted(
        path
        for path in root.glob(glob)
        if path.is_file()
        and not _under_pruned_dir(path, root)
        and not venv_prune.is_under_virtualenv(path, root)
        and not should_exclude(path, excludes)
    )


def missing_files(files: Sequence[Path]) -> list[Path]:
    """The named *files* that are not regular files, in the order given.

    Checked before any file is read: reading one later raises ``FileNotFoundError``, the
    same class a missing shellcheck raises, and the caller would report the wrong fault.
    """
    return [path for path in files if not path.is_file()]


def select_named_files(
    files: Sequence[Path], cli_excludes: Sequence[Path], *, stop: Path | None = None
) -> list[Path]:
    """The *files* that no exclude binds, sorted and each listed once.

    Used for files named on the command line and for the files ``--root`` discovered, so
    the two modes apply excludes identically. No prune by directory name or virtualenv
    applies here: a named file is one the caller chose, and discovery has already pruned.
    The excludes still bind, as ruff's ``--force-exclude`` does for a pre-commit hook. Each
    file's standing excludes come from the nearest ``pyproject.toml`` configuring this tool
    (:func:`read_config_excludes`), since files can sit under different projects; each directory's answer is read once per call. A file
    listed twice (under two spellings, too) is kept once, under the spelling given
    first, because that is the one the caller will look for in the report. *stop* bounds
    the walk, as in :func:`read_config_excludes`. Raises :class:`ConfigError` for config it
    cannot trust.
    """
    config_by_dir: dict[Path, tuple[Path, ...]] = {}
    kept: dict[Path, Path] = {}
    for path in files:
        resolved = path.resolve()
        if resolved in kept:
            continue
        if path.parent not in config_by_dir:
            config_by_dir[path.parent] = read_config_excludes(path.parent, stop=stop)
        if not should_exclude(path, [*config_by_dir[path.parent], *cli_excludes]):
            kept[resolved] = path
    return sorted(kept.values())


def nearest_rcfile(start: Path, *, stop: Path | None = None) -> Path | None:
    """The ShellCheck rc file ShellCheck itself would find for a script in *start*.

    Walks *start* and each parent up to the filesystem root, trying :data:`RCFILE_NAMES`
    in order in each directory, and returns the first regular file found. ``None`` means
    the project has none, and then ShellCheck's own fallback (the user's home and XDG
    config) applies. *stop* (inclusive, when it is an ancestor) bounds the walk so tests
    do not climb into a real rc file.
    """
    here = start.resolve()
    chain: tuple[Path, ...] = (here, *here.parents)
    if stop is not None and stop.resolve() in chain:
        chain = chain[: chain.index(stop.resolve()) + 1]
    for directory in chain:
        for name in RCFILE_NAMES:
            candidate = directory / name
            if candidate.is_file():
                return candidate
    return None


def read_shell_blocks(
    md_files: Sequence[Path], langs: Sequence[str]
) -> list[ShellBlock]:
    """The shell blocks in *md_files*; raises :class:`UnreadableMarkdown` naming each failure.

    ``utf-8-sig`` so a byte-order mark is dropped rather than glued to the first line, where
    it would stop a fence there from being seen. Every file is tried before raising, so one
    run names every unreadable file rather than one per attempt.
    """
    blocks: list[ShellBlock] = []
    unreadable: list[str] = []
    for md in md_files:
        try:
            text = md.read_text(encoding="utf-8-sig")
        except (UnicodeDecodeError, OSError) as exc:
            unreadable.append(f"  {md}: {exc}")
            continue
        blocks.extend(extract_blocks(text, md, langs))
    if unreadable:
        raise UnreadableMarkdown(
            f"cannot read {len(unreadable)} Markdown file(s) as UTF-8 text:\n"
            + "\n".join(unreadable)
        )
    return blocks
