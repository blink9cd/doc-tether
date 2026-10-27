"""Which files this gate checks, and the config that prunes them.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

The two halves belong together because they answer one question -- *what is in scope* --
from opposite directions: the config supplies the standing exclusions, and the walk
applies them alongside the built-in prunes. Splitting them would put the reader of
``[tool.cli_doc_consistency] exclude`` in one file and its only consumer in another.

What counts as a CLI is *not* decided here: the walk asks ``clidoc_astcli`` and keeps the
files it says yes to. That one predicate is the whole difference between "this file is in
scope" and "this file is a subject", and it belongs with the rest of the AST reading.

``read_config_excludes`` resolves the pyproject by walking up from the caller's *target*
path, never from the process working directory, so the verdict is a function of what the
tool was pointed at rather than where it was launched. That
property lives in the ``start`` parameter, so it survives this module's location.

Entry point assumptions
-----------------------
Reads the filesystem (a directory walk and the config read) through injected seams, so
tests never touch the real tree. No streams and no subprocesses. The two sibling
libraries it needs are bootstrapped here rather than inherited from the parent's
``sys.path`` inserts, so importing this module on its own needs no import order.
"""

from __future__ import annotations

import ast
import os
import sys
import tokenize
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from clidoc_astcli import is_cli_module
from clidoc_model import TargetError

# venv_prune owns the name-independent virtualenv test (PEP 405's pyvenv.cfg marker) --
# _DEFAULT_EXCLUDE_DIRS below names only ".venv", so without it every third-party CLI
# module under site-packages is checked for a companion doc. pyproject_config owns the one
# nearest-pyproject [tool.<name>] read -- the walk, the parse, the stop-the-gate rule for
# config that cannot be trusted, and what a resolved entry means (a subtree). Both are
# sibling per-tool folders, resolved from this file's own location two levels up.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "venv_prune"))
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent / "pyproject_config")
)

import pyproject_config  # noqa: E402 - import follows the sys.path bootstrap above
import venv_prune  # noqa: E402 - import follows the sys.path bootstrap above


# --- Discovery --------------------------------------------------------------
# Caches and environments, plus packaging output: `pip install .` leaves build/lib/ copies
# of every CLI (each without its doc), and dist/, .tox/ and .nox/ hold the same kind of
# derived tree, so walking them reports drift in files nobody edits.
_DEFAULT_EXCLUDE_DIRS: frozenset[str] = frozenset(
    {
        ".git",
        "node_modules",
        "__pycache__",
        ".mypy_cache",
        ".ruff_cache",
        ".venv",
        "build",
        "dist",
        ".tox",
        ".nox",
    }
)


_PY_SUFFIX = ".py"


_MD_SUFFIX = ".md"


_TEST_PREFIX = "test_"


_CONFIG_TABLE = "cli_doc_consistency"  # the pyproject [tool.<name>] table read


_EXCLUDE_KEY = (
    "exclude"  # the [tool.cli_doc_consistency] key holding standing prune paths
)


_RUN_HELP_KEY = "run_help"  # opt in to executing the target's --help for defaults


_CHECK_POSITIONALS_KEY = "check_positionals"  # opt in to Check D


#: pyproject_config's exit-2 class, re-exported so this name stays on the module's
#: public surface; the type is pyproject_config's, because that is where config is read.
ConfigError = pyproject_config.ConfigError


# ---------------------------------------------------------------------------
# Config: [tool.cli_doc_consistency].exclude from the nearest pyproject.
# ---------------------------------------------------------------------------
def read_config_excludes(
    start: Path,
    *,
    is_file: pyproject_config.IsFileFn = pyproject_config.default_is_file,
    read_text: pyproject_config.ReadTextFn = pyproject_config.default_read_text,
) -> tuple[Path, ...]:
    """Resolved ``[tool.cli_doc_consistency] exclude`` paths from the nearest pyproject above *start*.

    The walk, the parse, the list-of-strings validation and the
    resolve-against-the-pyproject's-own-directory rule belong to ``pyproject_config``;
    what is left here is which table and key this gate reads, and that the result is an
    ordered tuple. No pyproject / no table / no key -> ``()``; a malformed file or a
    non-list / non-string value raises :class:`ConfigError`.

    The seams carry defaults rather than being required, so the reader stays callable as
    a plain ``Callable[[Path], Sequence[Path]]``. Tests inject fakes through them rather
    than bounding the climb with a ``stop=`` ceiling: a faked filesystem never reaches a
    real pyproject at all, where a ceiling only keeps the climb short of one.
    """
    lists = pyproject_config.read_path_lists(
        start,
        table=_CONFIG_TABLE,
        keys=(_EXCLUDE_KEY,),
        is_file=is_file,
        read_text=read_text,
    )
    return tuple(lists[_EXCLUDE_KEY])


@dataclass(frozen=True)
class ConfigSwitches:
    """The two opt-ins ``[tool.cli_doc_consistency]`` may turn on; both off by default."""

    run_help: bool
    check_positionals: bool


def _require_bool(values: dict[str, object], key: str) -> bool:
    """*key*'s value as a bool (absent -> False); anything else is a :class:`ConfigError`.

    Strict on purpose: a quoted ``"true"`` or a ``1`` read as truthy would work by
    accident, and a ``"false"`` read the same way would switch a check ON while saying
    off -- so a value that is not a TOML boolean stops the gate instead.
    """
    value = values.get(key, False)
    if not isinstance(value, bool):
        raise ConfigError(
            f"[tool.{_CONFIG_TABLE}].{key} must be true or false, "
            f"not {type(value).__name__} {value!r}"
        )
    return value


def read_config_switches(
    start: Path,
    *,
    is_file: pyproject_config.IsFileFn = pyproject_config.default_is_file,
    read_text: pyproject_config.ReadTextFn = pyproject_config.default_read_text,
) -> ConfigSwitches:
    """``run_help`` and ``check_positionals`` from the nearest pyproject above *start*.

    The same nearest-file walk, parse and malformed-stops rule as the exclude read,
    through ``pyproject_config.read_tool_table``; that library deliberately owns path
    lists only, so validating these scalars is this module's job. A missing pyproject,
    table or key leaves the switch off.
    """
    found = pyproject_config.read_tool_table(
        start, table=_CONFIG_TABLE, is_file=is_file, read_text=read_text
    )
    values = found[1] if found is not None else {}
    return ConfigSwitches(
        run_help=_require_bool(values, _RUN_HELP_KEY),
        check_positionals=_require_bool(values, _CHECK_POSITIONALS_KEY),
    )


# ---------------------------------------------------------------------------
# Orchestration.
# ---------------------------------------------------------------------------
def _read_text(path: Path) -> str:
    """Read a doc as UTF-8; an undecodable one raises :class:`UnicodeDecodeError`."""
    return path.read_text(encoding="utf-8")


def _read_source(path: Path) -> str:
    """Read Python source the way Python does: its coding cookie or BOM, else UTF-8.

    An undecodable file raises :class:`UnicodeDecodeError`, and a malformed cookie a
    :class:`SyntaxError`, which the callers below turn into a skip or an exit 2.
    """
    with tokenize.open(path) as handle:
        return handle.read()


# What reading and parsing one source file can raise on hostile or merely unusual
# input, beside the ordinary OSError / SyntaxError: a coding cookie naming a codec that
# is not a text encoding (LookupError), and a source deep enough to overflow the
# parser's stack (MemoryError, "Parser stack overflowed") or the compiler's
# (RecursionError). ValueError covers an undecodable byte (UnicodeDecodeError is one)
# and the null-byte refusal of older interpreters. Each is a property of that one file.
_UNPARSEABLE = (
    OSError,
    SyntaxError,
    ValueError,
    LookupError,
    MemoryError,
    RecursionError,
)


def parse_source(path: Path, read_text: Callable[[Path], str]) -> ast.Module:
    """Read and parse one Python file, or raise :class:`TargetError` saying why not."""
    try:
        return ast.parse(read_text(path))
    except OSError as exc:
        raise TargetError(f"cannot read {path}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise TargetError(f"cannot decode {path}: {exc}") from exc
    except _UNPARSEABLE as exc:
        raise TargetError(f"cannot parse {path}: {type(exc).__name__}: {exc}") from exc


def discover_cli_files(
    path: Path, excludes: Sequence[Path], *, read_text: Callable[[Path], str]
) -> list[Path]:
    """Find CLI ``.py`` files: the named file itself, or a walk pruning excludes.

    A directly-named file that cannot be read, decoded or parsed raises
    :class:`TargetError` (exit-2). In a directory walk, ``test_*.py`` files, files that
    cannot be read, decoded or parsed, and non-CLI modules are silently skipped, so one
    bad file never wedges the gate.
    """
    if path.is_file():
        return [path] if is_cli_module(parse_source(path, read_text)) else []
    found: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(path):
        here = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in _DEFAULT_EXCLUDE_DIRS
            and not venv_prune.is_virtualenv(here / d)
            and not pyproject_config.is_under(here / d, excludes)
        ]
        for name in filenames:
            candidate = here / name
            if candidate.suffix != _PY_SUFFIX or name.startswith(_TEST_PREFIX):
                continue
            if pyproject_config.is_under(candidate, excludes):
                continue
            try:
                tree = parse_source(candidate, read_text)
            except TargetError:
                continue  # one bad file never wedges the walk
            if is_cli_module(tree):
                found.append(candidate)
    return sorted(found)
