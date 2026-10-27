"""Parse the two human-readable renderings of a CLI into option-to-default maps.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

The two artifacts are the live ``--help`` text and the companion doc's option surface:
its argument tables and any pasted ``--help`` block, which is read by the same parser as
the live text. They are read side by side here because they are the same kind of work on the same
subject -- recover structure from formatted prose that a formatter or an author may lay out
differently on any given day -- and because the module that *compares* them wants both or
neither. Their import sets agree too, which is the corroborating signal that this is one
concern rather than two.

Only the *argument tables*, the pasted ``--help`` blocks and the exit-code section of the
doc are read here. Check C reads the same file for a stated version floor, and does it in
``clidoc_floor`` beside the implied floor it compares against -- so "the ``.md`` parser" is
not one place, deliberately.

Both parsers are deliberately conservative: where the layout cannot be read with
confidence, they return nothing rather than a guess, and the caller downgrades the result
to advisory. A parser here that guessed would turn Check B from a gate into a nuisance.

Entry point assumptions
-----------------------
Pure functions over already-read text: no filesystem reads, no subprocesses, no streams,
no sibling libraries. Rendering the ``--help`` text is a subprocess and stays in the
parent, where the suite pins it. Importing this module on its own needs no import order.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator, Sequence
from collections.abc import Set as AbstractSet

from clidoc_model import MdArgTable


# A long option name, as every reader here takes it: a rendered help entry, a table row's
# first cell and a pasted help block's option column all yield names by this one token.
_LONG_OPTION_RE = re.compile(r"--[a-zA-Z][\w-]*")


# A table's shorthand for a BooleanOptionalAction pair: ``--[no-]fancy`` names both
# ``--fancy`` and ``--no-fancy``, the two strings argparse registers for it.
_NEGATABLE_RE = re.compile(r"--\[no-\]([a-zA-Z][\w-]*)")


# What separates a help entry's option column from its help text: argparse pads the
# column with at least two spaces, and an entry whose help wraps to the next line is
# joined with two spaces too (see _iter_help_entries).
_COLUMN_GAP_RE = re.compile(r"\s{2,}")


def _option_column_name(entry: str) -> str | None:
    """The first long option in a help entry's OPTION COLUMN, or None.

    Only the column is searched, never the help text after it: a short-only option
    whose help says "see --retries (default: 8)" must not be read as --retries. Shared
    by the live-render reader and the pasted-block reader, so both sides name alike.
    """
    match = _LONG_OPTION_RE.search(_COLUMN_GAP_RE.split(entry, maxsplit=1)[0])
    return match.group(0) if match is not None else None


# The marker argparse's ArgumentDefaultsHelpFormatter appends a default under.
_DEFAULT_MARKER = "(default:"


# The longest a joined help entry may grow. An option's help runs to a few hundred
# characters; a pasted block of thousands of continuation lines is not help text, and
# capping it keeps every per-entry read bounded however the input is shaped.
_MAX_HELP_ENTRY_CHARS = 4096


def _rendered_default(entry: str) -> str | None:
    """The default in the entry's LAST ``(default: X)``, which must close the entry.

    Found with one ``rfind`` and a slice, never a regex scan from each marker: help
    text holding thousands of markers made that scan quadratic.
    """
    start = entry.rfind(_DEFAULT_MARKER)
    if start < 0:
        return None
    tail = entry[start + len(_DEFAULT_MARKER) :].rstrip()
    if not tail.endswith(")"):
        return None
    return tail[:-1].strip()


def parse_help_defaults(help_text: str) -> dict[str, str]:
    """Map each option to the ``(default: X)`` value argparse rendered for it.

    Parses the ``options:`` block, joins each option's wrapped lines, and reads
    the trailing ``(default: ...)`` the formatter appends. Options without a
    rendered default are omitted.
    """
    lines = help_text.splitlines()
    start = _options_block_start(lines)
    if start is None:
        return {}
    defaults: dict[str, str] = {}
    for entry in _iter_help_entries(lines[start + 1 :]):
        name = _option_column_name(entry)
        default = _rendered_default(entry)
        if name is not None and default is not None:
            defaults[name] = default
    return defaults


def _options_block_start(lines: Sequence[str]) -> int | None:
    """Index of the ``options:`` / ``optional arguments:`` header line, or None."""
    for index, line in enumerate(lines):
        if line.strip().rstrip(":").lower() in ("options", "optional arguments"):
            return index
    return None


def _iter_help_entries(block: Sequence[str]) -> Iterator[str]:
    """Yield each option entry (option line + wrapped help joined) from a help block.

    A new entry starts at a line indented by exactly two spaces (argparse's option
    indent); more-indented lines continue the current entry, joined with two spaces so
    the option column stays separable from help text that wrapped onto its own line.
    The block ends at the first non-indented (blank or epilog) line. An entry is cut at
    :data:`_MAX_HELP_ENTRY_CHARS`; its option column, which leads it, always survives.
    """
    parts: list[str] = []
    size = 0
    for line in block:
        if not line.startswith(" "):
            break
        if re.match(r"^ {2}\S", line):
            if parts:
                yield "  ".join(parts)[:_MAX_HELP_ENTRY_CHARS]
            parts, size = [line.strip()], len(line)
        elif parts and size < _MAX_HELP_ENTRY_CHARS:
            parts.append(line.strip())
            size += len(line)
    if parts:
        yield "  ".join(parts)[:_MAX_HELP_ENTRY_CHARS]


def _split_row(line: str) -> list[str]:
    r"""Split a Markdown table row into stripped cells, honoring escaped ``\\|``."""
    inner = line.strip().strip("|")
    cells = re.split(r"(?<!\\)\|", inner)
    return [cell.replace("\\|", "|").strip() for cell in cells]


def _is_table_row(line: str) -> bool:
    """True when *line* is a Markdown table row (its stripped form starts with ``|``)."""
    return line.strip().startswith("|")


def _is_separator_cells(cells: Sequence[str]) -> bool:
    """True when *cells* are all table-separator cells (``---`` / ``:--:``).

    ``_split_row`` always yields at least one cell, so an empty *cells* (which
    would make ``all`` vacuously true) never occurs here.
    """
    return all(re.fullmatch(r":?-+:?", c) is not None for c in cells)


def _is_arg_header(cell: str) -> bool:
    """True when a header's first cell names an arguments/options/flags column."""
    text = cell.strip().strip("`").strip()
    return re.fullmatch(r"(?i)(arguments?|options?|flags?)", text) is not None


def _default_column_index(header: Sequence[str]) -> int | None:
    """Index of the ``Default`` column in a header row, or None when absent."""
    for index, cell in enumerate(header):
        if re.fullmatch(r"(?i)default", cell.strip().strip("`").strip()):
            return index
    return None


def _build_arg_table(
    header: Sequence[str], data: Sequence[Sequence[str]]
) -> MdArgTable:
    """Assemble an :class:`MdArgTable` from a header row and its data rows."""
    default_col = _default_column_index(header)
    options: dict[str, str | None] = {}
    # Each row comes from _split_row, which always yields at least one cell, so
    # row[0] is always safe.
    for row in data:
        cell = (
            row[default_col]
            if default_col is not None and default_col < len(row)
            else None
        )
        first_cell = _NEGATABLE_RE.sub(r"--\1 --no-\1", row[0])
        for match in _LONG_OPTION_RE.finditer(first_cell):
            options[match.group(0)] = cell
    return MdArgTable(options=options, has_default_column=default_col is not None)


def parse_md_arg_tables(md_text: str) -> list[MdArgTable]:
    """Every CLI-arguments table in *md_text* (empty list when none are found).

    An arguments table is a Markdown table whose first header cell reads
    ``Argument`` / ``Option`` / ``Flag`` (any case). A subparser tool documents
    its options across one table per subcommand (``### setup arguments`` ...), so
    all such tables are collected -- Check B matches an option against their union.
    """
    lines = md_text.splitlines()
    tables: list[MdArgTable] = []
    index = 0
    while index < len(lines) - 1:
        if _is_table_row(lines[index]) and _is_table_row(lines[index + 1]):
            header = _split_row(lines[index])
            if _is_separator_cells(_split_row(lines[index + 1])):
                data: list[list[str]] = []
                cursor = index + 2
                while cursor < len(lines) and _is_table_row(lines[cursor]):
                    data.append(_split_row(lines[cursor]))
                    cursor += 1
                # _split_row always yields >=1 cell, so header[0] is safe.
                if _is_arg_header(header[0]):
                    tables.append(_build_arg_table(header, data))
                index = cursor
                continue
        index += 1
    return tables


def _documented_names(tables: Sequence[MdArgTable]) -> set[str]:
    """The union of option names documented across *tables* (minus ``--help``)."""
    names: set[str] = set()
    for table in tables:
        names |= set(table.options)
    names.discard("--help")
    return names


def _option_default_cell(
    tables: Sequence[MdArgTable], name: str
) -> tuple[bool, str | None]:
    """The documented default cell for *name*, and whether it is unambiguous.

    Returns ``(True, cell)`` only when *name* appears in exactly one table that
    has a ``Default`` column -- a subparser tool repeats an option (e.g. ``--pat``)
    across subcommand tables, and a default documented in several places is not an
    unambiguous single value, so it is skipped (``(False, None)``).
    """
    cells = [
        t.options[name] for t in tables if t.has_default_column and name in t.options
    ]
    if len(cells) == 1:
        return True, cells[0]
    return False, None


# A fence opener: up to three spaces of indent, then three or more backticks or tildes,
# then an info string (any, including none). CommonMark's own shape.
_FENCE_OPEN_RE = re.compile(r"^( {0,3})(`{3,}|~{3,})")


# What a pasted --help block's first non-blank line starts with.
_USAGE_PREFIX = "usage:"


# The command a README shows before the pasted output: an optional shell prompt
# character, a command, and --help or -h as its last word. Accepted only when the next
# non-blank line starts with usage:, so a block of other commands is not mistaken for one.
_HELP_PROMPT_RE = re.compile(r"^\s*(?:[$%>#]\s*)?\S.*\s(?:--help|-h)\s*$")


# One option entry in a --help block: argparse indents each by exactly two spaces, and an
# option entry's first token is an option string. A wrapped help line is indented further.
_HELP_OPTION_LINE_RE = re.compile(r"^ {2}(-\S.*)$")


# argparse's own option-section headings: a column-0 line whose title ends in
# "arguments", "options" or "flags" -- "options:" / "optional arguments:" / "positional
# arguments:", and argument groups titled that way. A heading equal to an argument-group
# title the AST declares opens a section too (see _is_section_heading). Option entries
# are read ONLY inside a section, which runs until the next column-0 line, so an indented
# example under an epilog heading ("examples:") is not mistaken for an option.
_OPTION_SECTION_RE = re.compile(r"(?i)^(?:\S.*\s)?(?:arguments|options|flags):\s*$")


def _fence_closer(fence: str) -> re.Pattern[str]:
    """The line that closes a block opened by *fence*.

    That is the same character, at least as many of it, and nothing else.
    """
    return re.compile(
        r"^ {0,3}" + re.escape(fence[0]) + "{" + str(len(fence)) + r",}\s*$"
    )


def _fenced_blocks(lines: Sequence[str]) -> Iterator[list[str]]:
    """Yield each fenced code block's body lines, with the fence's own indent removed.

    A block closes at a line of the SAME fence character, at least as long as the opener
    and carrying nothing else; a shorter or different fence inside it is content. An
    unclosed block runs to the end of the document, as CommonMark has it.
    """
    index = 0
    while index < len(lines):
        opener = _FENCE_OPEN_RE.match(lines[index])
        index += 1
        if opener is None:
            continue
        indent, closer = len(opener.group(1)), _fence_closer(opener.group(2))
        body: list[str] = []
        while index < len(lines) and not closer.match(lines[index]):
            line = lines[index]
            body.append(line[min(indent, len(line) - len(line.lstrip(" "))) :])
            index += 1
        index += 1  # step over the closing fence (or past the end)
        yield body


def _is_usage_line(line: str) -> bool:
    """True when *line* starts with ``usage:`` (any case, leading space ignored)."""
    return line.lstrip().lower().startswith(_USAGE_PREFIX)


def _is_help_block(body: Sequence[str]) -> bool:
    """True when the block opens with ``usage:``, or with a ``--help`` prompt line then it.

    The prompt form is the README habit of showing the command too:
    ``$ tool --help`` followed by its output.
    """
    lines = [line for line in body if line.strip()]
    if not lines:
        return False
    if _is_usage_line(lines[0]):
        return True
    return (
        len(lines) > 1
        and _HELP_PROMPT_RE.match(lines[0]) is not None
        and _is_usage_line(lines[1])
    )


def _is_section_heading(line: str, group_headings: AbstractSet[str]) -> bool:
    """True when column-0 *line* opens an option section.

    Either argparse's own heading shape (:data:`_OPTION_SECTION_RE`), or ``<title>:``
    for an argument-group title the CLI declares -- compared case-insensitively, trailing
    whitespace ignored, against *group_headings* (already lowercased, colon included).
    """
    return (
        _OPTION_SECTION_RE.match(line) is not None
        or line.rstrip().lower() in group_headings
    )


def _option_entries(
    body: Sequence[str], group_headings: AbstractSet[str]
) -> Iterator[str]:
    """Yield the option column-and-help text of each entry inside an option section.

    A section opens at a heading :func:`_is_section_heading` accepts and closes at the
    next column-0 line; blank lines inside it (an argument group's description) do not
    close it. Anything outside a section -- the description, the epilog and its
    examples -- is never read as an option.
    """
    in_section = False
    for line in body:
        if line.strip() and not line.startswith(" "):
            in_section = _is_section_heading(line, group_headings)
            continue
        entry = _HELP_OPTION_LINE_RE.match(line)
        if in_section and entry is not None:
            yield entry.group(1)


def _help_block_table(
    body: Sequence[str], group_headings: AbstractSet[str]
) -> MdArgTable:
    """Read one pasted ``--help`` block into the structure an arguments table becomes.

    Each option entry (see :func:`_option_entries`) yields ONE name: the first long
    option in its option column (the text before the first run of two spaces, so an
    option named in another option's help text is not mistaken for one). argparse
    renders option strings in the order they were declared, so that is exactly
    :class:`OptionInfo`'s ``name`` (the first long string of the ``add_argument``
    call); a later long alias is not a second option, and a short-only entry has no
    long name to match. Defaults are read by :func:`parse_help_defaults`, the same
    reader the live render goes through, so the two sides of the comparison parse
    alike.
    """
    defaults = parse_help_defaults("\n".join(body))
    options: dict[str, str | None] = {}
    for entry in _option_entries(body, group_headings):
        name = _option_column_name(entry)
        if name is not None:
            options[name] = defaults.get(name)
    return MdArgTable(options=options, has_default_column=bool(defaults))


def parse_md_help_blocks(
    md_text: str, group_titles: Iterable[str] = ()
) -> list[MdArgTable]:
    """Every pasted ``--help`` block in *md_text*, each read as an :class:`MdArgTable`.

    A help block is a fenced code block (backtick or tilde, any info string) whose first
    non-blank line starts with ``usage:``, or is a prompt line ending in ``--help`` /
    ``-h`` followed by a ``usage:`` line -- the README habit of pasting the tool's own
    help. Check B unions these with the arguments tables, so a doc may use either shape.

    *group_titles* are the argument-group titles the CLI declares
    (``clidoc_astcli.argument_group_titles``): a pasted heading ``<title>:`` opens an
    option section for each, since argparse prints a custom group under its own title.
    """
    group_headings = frozenset(f"{title.lower()}:" for title in group_titles)
    return [
        _help_block_table(body, group_headings)
        for body in _fenced_blocks(md_text.splitlines())
        if _is_help_block(body)
    ]


def _unfenced_flags(lines: Sequence[str]) -> list[bool]:
    """For each line, True when it lies OUTSIDE every fenced code block.

    A fence's own opening and closing lines count as inside. Used so a ``# comment`` in
    a fenced shell example is never taken for a Markdown heading.
    """
    flags: list[bool] = []
    closer: re.Pattern[str] | None = None
    for line in lines:
        if closer is not None:
            flags.append(False)
            if closer.match(line):
                closer = None
            continue
        opener = _FENCE_OPEN_RE.match(line)
        flags.append(opener is None)
        if opener is not None:
            closer = _fence_closer(opener.group(2))
    return flags


def _section_after_heading(md_text: str, heading_regex: str) -> str | None:
    """Text under the first heading matching *heading_regex*, up to the next heading.

    Headings are looked for outside fenced code blocks only, both the one that opens
    the section and the one that ends it.
    """
    lines = md_text.splitlines()
    outside = _unfenced_flags(lines)
    pattern = re.compile(r"^#+\s*" + heading_regex + r"\s*$", re.IGNORECASE)
    for index, line in enumerate(lines):
        if outside[index] and pattern.match(line.strip()):
            body: list[str] = []
            for offset, following in enumerate(lines[index + 1 :], start=index + 1):
                if outside[offset] and re.match(r"^#+\s", following.strip()):
                    break
                body.append(following)
            return "\n".join(body)
    return None


def parse_md_exit_codes(md_text: str) -> set[int] | None:
    """The exit codes an ``## Exit codes`` section documents, or None if no section.

    Returning None (no dedicated section) means "not verifiable" -- Check B then
    skips exit-code checking rather than guessing from scattered prose.
    """
    section = _section_after_heading(md_text, r"exit codes?")
    if section is None:
        return None
    codes = {int(m.group(1)) for m in re.finditer(r"`([0-9])`", section)}
    codes |= {int(m.group(1)) for m in re.finditer(r"(?i)\bexit\s+([0-9])\b", section)}
    return codes or None
