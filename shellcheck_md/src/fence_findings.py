#!/usr/bin/env python3
"""What a fenced shell block and a shellcheck finding ARE, and how each crosses between them.

Private to ``shellcheck_md`` -- it lives in that folder's ``src/``, and nothing outside this
tool folder may import it. The name is prefixed rather than a bare ``findings.py``
because a tool's ``src/`` goes on ``sys.path`` as a DIRECTORY: two tools each
shipping a generic module name would collide in any process importing both, and the loser
would be shadowed silently.

Grouped by being PURE, which is also the seam that makes the parent testable. Lifting a
block out of Markdown, rendering it as a script, reading one line of shellcheck's gcc output,
and mapping that line back to the Markdown it came from are all total functions over strings
-- so every case here is pinned with a literal rather than a temp directory or a subprocess.
What is left in the parent is exactly what is NOT pure: discovery, config, the run seam, the
argv budget, and the CLI.

The two records sit beside the functions that build them rather than in a model module of
their own. ``ShellBlock`` exists to answer ``remap_line``'s question -- which source line is
this script line? -- and a record whose meaning lives in another file drifts away from the
code that gives it that meaning.

Entry point assumptions
-----------------------
Pure: no filesystem access, no subprocess, no config reading, no stream writes. Reached
through the ``sys.path`` insert in ``shellcheck_md``, which resolves this directory from its
own location.
"""

from __future__ import annotations

import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

DEFAULT_LANGS: tuple[str, ...] = ("bash", "sh")

# A fence is matched by SHAPE, never by column. Anchoring the scan to column zero skips
# the normal spelling of a code block nested under a list item -- and skips it silently,
# because the file is still read and still counted, so the summary a reader checks to
# confirm coverage reads the same whether the block was linted or never seen.
#
# Both of CommonMark's fence characters are taken: a run of three or more backticks, or of
# three or more tildes. A closer must use the opener's character (see closes_fence).
#
# The opener takes ANY indentation rather than CommonMark's top-level maximum of three.
# A flat scanner cannot know which container a line sits in, so the choice is between
# over- and under-accepting, and the two are not symmetric: over-accepting lints a block
# CommonMark would call literal text, which surfaces as a spurious finding on the line
# that caused it, while under-accepting is the silent miss this shape exists to prevent.
# A line inside a blockquote starts with `>`, so it matches neither pattern: a fence in a
# blockquote is not linted (see shellcheck_md.md, "What counts as a block").
_OPEN_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>.*)$")
_CLOSE_RE = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})[ \t]*$")

# CommonMark forbids a backtick in a BACKTICK fence's info string (the line would be an
# inline code span instead); a tilde fence's info string may hold one.
_BACKTICK = "`"

# The stem of the Markdown file is kept in each generated script's name so a reader can
# trace a temp file back to its source, but only this much of it and only these
# characters: the serial already makes every name unique, and an unbounded stem could push
# the name past the filesystem's NAME_MAX (255 bytes on most), which fails the write.
_SCRIPT_STEM_MAX = 64
_UNSAFE_NAME_CHARS = re.compile(r"[^A-Za-z0-9._-]")

# CommonMark lets a closing fence sit up to three spaces further in than its opener. The
# bound is what keeps a deeply-indented run of backticks INSIDE a block from closing it
# early, which a bare "any indentation" closer test would do.
_MAX_CLOSER_EXTRA_INDENT = 3

_GCC_RE = re.compile(
    r"^(?P<path>.*?):(?P<line>\d+):(?P<column>\d+):\s+"
    r"(?P<severity>\w+):\s+(?P<message>.*?)\s+\[(?P<code>SC\d+)\]\s*$"
)


@dataclass(frozen=True)
class ShellBlock:
    """A fenced shell block lifted out of a Markdown file."""

    source: Path
    index: int  # 1-based position among shell blocks in `source`
    start_line: int  # 1-based line of the opening fence within `source`
    lang: str
    body: tuple[str, ...]  # already dedented by `body_indent` characters
    # Characters of common leading whitespace removed from every body line. Kept on the
    # record because it is the ONLY thing that puts a finding's column back where a reader
    # will click: the dedent is uniform across the block, so the correction is exact rather
    # than a guess. Defaults to zero so a flush block -- and every construction that
    # predates nesting support -- describes itself truthfully with no argument.
    body_indent: int = 0


@dataclass(frozen=True)
class Finding:
    """A shellcheck finding, remapped onto its Markdown source location."""

    source: Path
    line: int  # 1-based line within the Markdown source
    column: int
    severity: str
    code: str
    message: str


class _ParsedLine(NamedTuple):
    """One parsed line of shellcheck gcc-format output (script-relative)."""

    path: str
    line: int
    column: int
    severity: str
    message: str
    code: str


class _FenceOpen(NamedTuple):
    """The four things about an opening fence that its closer must be judged against."""

    lang: str
    indent: int  # leading whitespace characters before the fence run
    length: int  # how many fence characters the run holds
    char: str  # the run's character: a backtick or a tilde


class BatchParse(NamedTuple):
    """One shellcheck invocation's output, read: what mapped back, and what did not.

    *unmapped* holds each gcc-format line naming a path this run never wrote. Kept rather
    than dropped because the run's verdict needs it: an exit status reporting findings is
    only believable when at least one of them mapped back to a Markdown file.
    """

    findings: tuple[Finding, ...]
    unmapped: tuple[str, ...]


def parse_fence_open(line: str, langs: Sequence[str]) -> _FenceOpen | None:
    """Return the opening fence `line` declares for one of `langs`, else None.

    Indentation is accepted and measured rather than rejected -- see ``_OPEN_RE`` for why
    any depth is taken. The language is the info string's FIRST word, as CommonMark reads
    it, so ```` ```bash title="x" ```` is a bash fence; it is compared without case, so
    ```` ```Bash ```` is one too, and the language returned is the case-folded word. A bare
    fence carrying no language, and one naming a language outside *langs*, are both
    non-openers.
    """
    match = _OPEN_RE.match(line)
    if match is None:
        return None
    fence, info = match["fence"], match["info"]
    if fence[0] == _BACKTICK and _BACKTICK in info:
        return None
    words = info.split()
    lang = words[0].casefold() if words else ""
    if lang not in {wanted.casefold() for wanted in langs}:
        return None
    return _FenceOpen(
        lang=lang, indent=len(match["indent"]), length=len(fence), char=fence[0]
    )


def fence_language(line: str, langs: Sequence[str]) -> str | None:
    """Return the fenced language if `line` opens a fence for one of `langs`.

    The language half of :func:`parse_fence_open`, kept because it is this module's
    published predicate for "does this line open a shell block?". A bare ``` is not an
    opener at any indentation.
    """
    opening = parse_fence_open(line, langs)
    return None if opening is None else opening.lang


def closes_fence(line: str, opening: _FenceOpen) -> bool:
    """Return True if `line` is a closing fence for `opening`.

    Judged AGAINST the opener rather than by a column-zero search, which is what lets an
    indented block end where it actually ends: the run must use the opener's character and
    at least as many of it, carry nothing but whitespace after them, and sit no more than
    :data:`_MAX_CLOSER_EXTRA_INDENT` further in.
    """
    match = _CLOSE_RE.match(line)
    if match is None:
        return False
    fence = match["fence"]
    if fence[0] != opening.char or len(fence) < opening.length:
        return False
    return len(match["indent"]) <= opening.indent + _MAX_CLOSER_EXTRA_INDENT


def _leading_whitespace(line: str) -> str:
    """The whitespace `line` opens with (the whole line, if it is all whitespace)."""
    return line[: len(line) - len(line.lstrip())]


def dedent_body(body: Sequence[str], cap: int) -> tuple[int, tuple[str, ...]]:
    """Strip the body's common leading whitespace, up to `cap`; return (width, lines).

    A nested block's indentation is Markdown's, not the script's, and handing it to
    shellcheck is not merely untidy: a heredoc terminator has to reach column zero, so an
    indented body turns a valid heredoc into an unterminated one. CommonMark removes at
    most the opening fence's own indentation, which is what *cap* carries -- a flush
    fence caps at zero and this is then a byte-exact no-op, so no finding that reports
    today can move.

    The width is capped by the common prefix of the NON-BLANK lines, so every non-blank
    line provably holds at least that many leading whitespace characters and the slice is
    total; a blank line is shorter and slices to empty, which is what it already meant.
    """
    prefixes = [_leading_whitespace(line) for line in body if line.strip()]
    width = min(cap, len(os.path.commonprefix(prefixes))) if prefixes else 0
    return width, tuple(line[width:] for line in body)


def extract_blocks(
    text: str, source: Path, langs: Sequence[str] = DEFAULT_LANGS
) -> list[ShellBlock]:
    r"""Return the fenced shell blocks in `text`, in document order.

    An unterminated final block (no closing fence before EOF) is still captured. Each
    block's body is dedented by the opening fence's indentation, and the amount removed
    is kept on the block so a finding's column can be put back (see :func:`dedent_body`).

    Lines are split on ``\n`` alone. ``str.splitlines`` also splits on a form feed, the
    file and group separators, NEL and U+2028/U+2029, none of which ends a Markdown line,
    so one of them in a file shifted every later finding's line number. *text* is expected
    with its newlines already translated (as ``read_text`` does), so ``\r\n`` is not seen.
    """
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()  # the empty piece after a final newline is not a line
    n = len(lines)
    blocks: list[ShellBlock] = []
    i = 0
    while i < n:
        opening = parse_fence_open(lines[i], langs)
        if opening is None:
            i += 1
            continue
        start_line = i + 1
        i += 1
        raw: list[str] = []
        while i < n and not closes_fence(lines[i], opening):
            raw.append(lines[i])
            i += 1
        width, body = dedent_body(raw, opening.indent)
        blocks.append(
            ShellBlock(
                source=source,
                index=len(blocks) + 1,
                start_line=start_line,
                lang=opening.lang,
                body=body,
                body_indent=width,
            )
        )
        i += 1  # step past the closing fence (or off the end at EOF)
    return blocks


def script_name(serial: int, block: ShellBlock) -> str:
    """A unique, traceable temp-script filename for *block*, *serial* making it unique.

    The source's stem is cut to :data:`_SCRIPT_STEM_MAX` characters and every character
    outside ``[A-Za-z0-9._-]`` becomes ``_``, so the name stays ASCII and short whatever
    the Markdown file is called.
    """
    stem = _UNSAFE_NAME_CHARS.sub("_", block.source.stem[:_SCRIPT_STEM_MAX])
    return f"{serial:04d}_{stem}__blk{block.index}__L{block.start_line}.sh"


def block_to_script(block: ShellBlock) -> str:
    """Render `block` as a script: shebang + LF-joined body + trailing newline."""
    return "\n".join([f"#!/usr/bin/env {block.lang}", *block.body]) + "\n"


def remap_line(block: ShellBlock, script_line: int) -> int:
    """Map a 1-based line in the generated script back to the Markdown source.

    Script line 1 is the synthesized shebang; line 2 is the first body line,
    which lives at ``start_line + 1`` in the source. Findings on the shebang map
    to the opening-fence line.
    """
    if script_line <= 1:
        return block.start_line
    return block.start_line + (script_line - 1)


def remap_column(block: ShellBlock, script_line: int, script_column: int) -> int:
    """Map a column in the generated script back to the Markdown source.

    Dedenting a nested block moves every body column left by ``body_indent``; this puts
    it back, so a reported ``file:line:column`` still points at the character that caused
    the finding. Script line 1 is the synthesized shebang -- no Markdown column
    corresponds to it, so its column is passed through untouched rather than shifted onto
    a position that means nothing.
    """
    if script_line <= 1:
        return script_column
    return script_column + block.body_indent


def parse_shellcheck_line(line: str) -> _ParsedLine | None:
    """Parse one gcc-format shellcheck line, or None if it is not a finding."""
    match = _GCC_RE.match(line)
    if match is None:
        return None
    return _ParsedLine(
        path=match["path"],
        line=int(match["line"]),
        column=int(match["column"]),
        severity=match["severity"],
        message=match["message"],
        code=match["code"],
    )


def _norm(path: str) -> str:
    """Normalize a path for case- and separator-insensitive lookup."""
    return os.path.normcase(os.path.normpath(path))


def _findings_from_output(
    output: str, path_to_block: dict[str, ShellBlock]
) -> BatchParse:
    """Turn shellcheck output into source-mapped findings, setting aside the rest.

    Called once per argv batch rather than once per run, so *output* is one
    invocation's combined streams and *path_to_block* spans every batch. A line that is
    not a gcc-format finding is skipped; a finding on a path no block was written to is
    kept in ``unmapped`` for the verdict (see :class:`BatchParse`).
    """
    findings: list[Finding] = []
    unmapped: list[str] = []
    for line in output.splitlines():
        parsed = parse_shellcheck_line(line)
        if parsed is None:
            continue
        block = path_to_block.get(_norm(parsed.path))
        if block is None:
            unmapped.append(line.strip())
            continue
        findings.append(
            Finding(
                source=block.source,
                line=remap_line(block, parsed.line),
                column=remap_column(block, parsed.line, parsed.column),
                severity=parsed.severity,
                code=parsed.code,
                message=parsed.message,
            )
        )
    return BatchParse(tuple(findings), tuple(unmapped))
