#!/usr/bin/env python3
"""Where the extracted scripts are written, so ShellCheck finds each project's rc file.

Private to ``shellcheck_md``: it lives in that folder's ``src/``, and nothing outside this
tool folder may import it. The name is specific rather than a bare ``scratch.py`` for
the reason ``fence_findings`` gives: a tool's ``src/`` goes on ``sys.path`` as a
directory, so a generic module name would collide in a process importing two tools.

ShellCheck looks for ``.shellcheckrc`` (then ``shellcheckrc``) beside the script it checks
and in each parent directory. The extracted scripts live in a temporary directory, which no
project's rc file sits above. So the scripts are grouped by the rc file nearest their
Markdown source, each group is written to its own subdirectory, and that group's rc file is
copied into it as ``.shellcheckrc``; scripts whose source has no rc file get a subdirectory
with none, and ShellCheck's own fallback (the user's rc file) applies to them as it would
to any script. ``--rcfile`` would have said the same thing more directly, but it arrived
only in ShellCheck 0.10.0, and the copy works on every version that reads rc files.

Every fault writing here is a :class:`ScratchWriteError`, never an ``OSError``: the caller
reads a bare ``OSError`` as "shellcheck could not be started", and a full disk reported as a
missing backend sends a reader to reinstall ShellCheck.

Entry point assumptions
-----------------------
Reached through the ``sys.path`` insert in ``shellcheck_md``. Writes only inside the
directory it is handed or creates; starts no process and writes no stream.
"""

from __future__ import annotations

import shutil
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path

from fence_findings import ShellBlock, _norm, block_to_script, script_name

# The name each group's rc copy takes: the one ShellCheck tries first in a directory, so
# the copy wins whichever of the two names the project's own file used.
RC_COPY_NAME = ".shellcheckrc"


class ScratchWriteError(Exception):
    """The temporary scripts could not be written, or an rc file was refused (a symlink).

    Either way an exit-2 condition whose message names the path.
    """


def scratch_directory() -> tempfile.TemporaryDirectory[str]:
    """A fresh temporary directory for one run; raises :class:`ScratchWriteError`."""
    try:
        return tempfile.TemporaryDirectory()
    except OSError as exc:
        raise ScratchWriteError(
            f"could not create a temporary directory for the extracted scripts: {exc}"
        ) from exc


def _group_directory(directory: Path, serial: int, rcfile: Path | None) -> Path:
    """Make group *serial*'s subdirectory, holding a copy of *rcfile* when there is one."""
    folder = directory / f"group{serial:03d}"
    try:
        folder.mkdir()
    except OSError as exc:
        raise ScratchWriteError(
            f"could not write the extracted scripts' folder {folder}: {exc}"
        ) from exc
    if rcfile is not None:
        # Copying follows a symlink, so a link would read whatever file it names
        # (outside the project, possibly) into the temporary directory. Refused, not
        # skipped: skipping would silently drop the project's directives.
        if rcfile.is_symlink():
            raise ScratchWriteError(
                f"refusing a symlinked ShellCheck rc file: {rcfile}"
            )
        try:
            shutil.copyfile(rcfile, folder / RC_COPY_NAME)
        except OSError as exc:
            raise ScratchWriteError(
                f"could not copy the ShellCheck rc file {rcfile} into the temporary "
                f"directory: {exc}"
            ) from exc
    return folder


def _write_script(script: Path, block: ShellBlock) -> None:
    """Write *block* as *script*; raises :class:`ScratchWriteError`."""
    try:
        # newline="\n": shellcheck is a Unix tool and flags CRLF (SC1017); without
        # this, write_text would translate \n -> \r\n on Windows.
        script.write_text(block_to_script(block), encoding="utf-8", newline="\n")
    except OSError as exc:
        raise ScratchWriteError(
            f"could not write the extracted script {script}: {exc}"
        ) from exc


def stage_scripts(
    blocks: Sequence[ShellBlock],
    directory: Path,
    find_rcfile: Callable[[Path], Path | None],
) -> tuple[dict[str, ShellBlock], list[list[Path]]]:
    """Write each block as a script under *directory*, one subdirectory per rc file.

    Returns the lookup that maps a reported path back to its block, and the scripts
    grouped so that every script in a group shares one subdirectory, and so one rc
    file. *find_rcfile* names the rc file for a Markdown directory (or None); each
    directory is asked once. Groups come in the order their first script was written.
    """
    path_to_block: dict[str, ShellBlock] = {}
    groups: dict[Path | None, list[Path]] = {}
    folders: dict[Path | None, Path] = {}
    rc_by_dir: dict[Path, Path | None] = {}
    for serial, block in enumerate(blocks):
        source_dir = block.source.parent
        if source_dir not in rc_by_dir:
            rc_by_dir[source_dir] = find_rcfile(source_dir)
        rcfile = rc_by_dir[source_dir]
        if rcfile not in folders:
            folders[rcfile] = _group_directory(directory, len(folders), rcfile)
        script = folders[rcfile] / script_name(serial, block)
        _write_script(script, block)
        path_to_block[_norm(str(script))] = block
        groups.setdefault(rcfile, []).append(script)
    return path_to_block, list(groups.values())
