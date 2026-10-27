#!/usr/bin/env python3
"""The argument parser ``shellcheck_md`` builds its CLI on: every message safe for a terminal.

Private to ``shellcheck_md`` -- it lives in that folder's ``src/``, and nothing outside this
tool folder may import it. The name is specific rather than a bare ``parser.py`` for the
reason ``fence_findings`` gives: a tool's ``src/`` goes on ``sys.path`` as a
directory, so a generic module name would collide in a process importing two tools.

argparse quotes the command line in its usage and error text ("unrecognized arguments: ..."),
and a staged file name is command-line text this tool did not write. The shared
``stdio_utf8.EscapingArgumentParser`` escapes only what UTF-8 cannot encode; an escape
sequence or a direction override passes it unchanged and would drive the terminal or CI log
it lands in. So each line also goes through ``stdio_utf8.spell_unsafe``.

Entry point assumptions
-----------------------
Reached through the ``sys.path`` insert in ``shellcheck_md``, which also puts ``stdio_utf8``
on the path. Defines a class only; writes nothing until argparse prints a message.
"""

from __future__ import annotations

from typing import Protocol

import stdio_utf8


class MessageSink(Protocol):
    """Where argparse writes a message: anything with a text ``write``.

    The same shape the base parser accepts, restated because its own name is private; a
    narrower type here would break the override's substitutability.
    """

    def write(self, text: str, /) -> object:
        """Write *text*; argparse ignores the result."""


class TerminalSafeParser(stdio_utf8.EscapingArgumentParser):
    """The escaping parser, with each line of its messages spelled safe for a terminal.

    argparse's own line breaks stay real, so a usage block keeps its shape -- the same
    treatment ``shellcheck_md`` gives the messages it writes itself.
    """

    def _print_message(self, message: str, file: MessageSink | None = None) -> None:
        """Spell each line of *message* safe, then write it as the base class would."""
        lines = message.split("\n")
        super()._print_message(
            "\n".join(stdio_utf8.spell_unsafe(line) for line in lines), file
        )
