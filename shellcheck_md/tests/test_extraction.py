"""Grouped suite: lifting fenced shell out of Markdown.

The pure front half -- which fences open a shell block, what a block's body is, how it
renders as a script, and how a script line maps back to its Markdown line. Nothing here
touches the filesystem or shellcheck, so every case is a string.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above


class FenceLanguageTests(unittest.TestCase):
    def test_matches_listed_language(self) -> None:
        self.assertEqual(sut.fence_language("```bash", ("bash", "sh")), "bash")

    def test_strips_trailing_whitespace(self) -> None:
        self.assertEqual(sut.fence_language("```sh   ", ("bash", "sh")), "sh")

    def test_language_not_listed_is_none(self) -> None:
        self.assertIsNone(sut.fence_language("```python", ("bash", "sh")))

    def test_non_fence_is_none(self) -> None:
        self.assertIsNone(sut.fence_language("echo hi", ("bash",)))

    def test_bare_fence_is_none(self) -> None:
        self.assertIsNone(sut.fence_language("```", ("bash",)))

    def test_indented_fence_opens_a_block(self) -> None:
        self.assertEqual(sut.fence_language("  ```bash", ("bash", "sh")), "bash")

    def test_indented_bare_fence_is_still_none(self) -> None:
        self.assertIsNone(sut.fence_language("  ```", ("bash",)))


class ExtractBlocksTests(unittest.TestCase):
    def test_single_closed_block(self) -> None:
        blocks = sut.extract_blocks(
            "intro\n```bash\necho hi\n```\ntail\n", Path("d.md")
        )
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual((block.lang, block.start_line, block.index), ("bash", 2, 1))
        self.assertEqual(block.body, ("echo hi",))

    def test_skips_non_shell_fence_and_keeps_sh(self) -> None:
        blocks = sut.extract_blocks(
            "```python\nx=1\n```\n```sh\nls\n```\n", Path("d.md")
        )
        self.assertEqual([b.lang for b in blocks], ["sh"])

    def test_unterminated_block_at_eof(self) -> None:
        blocks = sut.extract_blocks("```bash\necho a\necho b\n", Path("d.md"))
        self.assertEqual(blocks[0].body, ("echo a", "echo b"))

    def test_empty_block(self) -> None:
        blocks = sut.extract_blocks("```bash\n```\n", Path("d.md"))
        self.assertEqual(blocks[0].body, ())

    def test_no_blocks(self) -> None:
        self.assertEqual(sut.extract_blocks("just prose\n", Path("d.md")), [])


class BlockToScriptTests(unittest.TestCase):
    @staticmethod
    def _block(lang: str, body: tuple[str, ...]) -> sut.ShellBlock:
        return sut.ShellBlock(Path("d.md"), 1, 1, lang, body)

    def test_bash_with_body(self) -> None:
        script = sut.block_to_script(self._block("bash", ("echo hi",)))
        self.assertEqual(script, "#!/usr/bin/env bash\necho hi\n")

    def test_sh_empty_body(self) -> None:
        self.assertEqual(
            sut.block_to_script(self._block("sh", ())), "#!/usr/bin/env sh\n"
        )


class RemapLineTests(unittest.TestCase):
    @staticmethod
    def _block() -> sut.ShellBlock:
        return sut.ShellBlock(Path("d.md"), 1, 10, "bash", ("echo",))

    def test_shebang_maps_to_fence(self) -> None:
        self.assertEqual(sut.remap_line(self._block(), 1), 10)

    def test_body_line_maps_past_fence(self) -> None:
        self.assertEqual(sut.remap_line(self._block(), 2), 11)


class IndentedFenceTests(unittest.TestCase):
    """An indented fence is still a fence, and a column-zero scan cannot see one.

    Indenting a fence is the normal spelling of a code block nested under a list item,
    and CommonMark also allows up to three spaces at the top level. A scanner anchored to
    column zero skips both silently: the file is still read and still counted, so the run
    reports a clean file rather than an unchecked one, and the file count -- the number a
    reader checks to confirm coverage -- is identical either way.
    """

    _COMMAND = "$HOME/x/python3 foo.py --bar"

    def test_top_level_fence_indented_three_spaces_opens_a_block(self) -> None:
        blocks = sut.extract_blocks(
            f"# A\n\n   ```bash\n   {self._COMMAND}\n   ```\n", Path("d.md")
        )
        self.assertEqual([b.lang for b in blocks], ["bash"])
        self.assertEqual(blocks[0].body, (self._COMMAND,))

    def test_list_nested_fence_is_extracted_with_body_dedented(self) -> None:
        blocks = sut.extract_blocks(
            f"# B\n\n- item:\n\n  ```bash\n  {self._COMMAND}\n  ```\n", Path("d.md")
        )
        self.assertEqual(len(blocks), 1)
        block = blocks[0]
        self.assertEqual(block.start_line, 5)
        self.assertEqual(block.body, (self._COMMAND,))
        self.assertEqual(block.body_indent, 2)

    def test_indented_and_flush_blocks_render_identical_scripts(self) -> None:
        """The minimal pair: the same command must reach shellcheck the same way."""
        flush = sut.extract_blocks(f"```bash\n{self._COMMAND}\n```\n", Path("a.md"))[0]
        indented = sut.extract_blocks(
            f"- item:\n\n  ```bash\n  {self._COMMAND}\n  ```\n", Path("b.md")
        )[0]
        self.assertEqual(sut.block_to_script(flush), sut.block_to_script(indented))

    def test_closing_fence_may_be_indented(self) -> None:
        """The closer is matched against the opener, not found by a column-zero search."""
        blocks = sut.extract_blocks(
            "  ```bash\n  echo hi\n  ```\n\nprose\n\n```bash\necho bye\n```\n",
            Path("d.md"),
        )
        self.assertEqual([b.body for b in blocks], [("echo hi",), ("echo bye",)])

    def test_shorter_run_does_not_close_a_longer_opening_fence(self) -> None:
        """CommonMark: a closer needs at least as many characters as its opener."""
        blocks = sut.extract_blocks(
            "````bash\necho a\n```\necho b\n````\n", Path("d.md")
        )
        self.assertEqual(blocks[0].body, ("echo a", "```", "echo b"))

    def test_run_indented_far_past_the_opener_does_not_close_it(self) -> None:
        """A closer may sit up to three spaces past its opener; beyond that it is body."""
        blocks = sut.extract_blocks(
            "```bash\necho a\n      ```\necho b\n```\n", Path("d.md")
        )
        self.assertEqual(blocks[0].body, ("echo a", "      ```", "echo b"))

    def test_flush_fence_never_dedents_its_body(self) -> None:
        """Regression guard: at indent zero nothing is stripped, so no column moves."""
        blocks = sut.extract_blocks(
            "```bash\nif true; then\n    echo indented\nfi\n```\n", Path("d.md")
        )
        self.assertEqual(blocks[0].body, ("if true; then", "    echo indented", "fi"))
        self.assertEqual(blocks[0].body_indent, 0)

    def test_blank_line_does_not_defeat_the_common_indent(self) -> None:
        """A blank line carries no indentation, so it must not drag the dedent to zero."""
        blocks = sut.extract_blocks(
            "  ```bash\n  echo a\n\n  echo b\n  ```\n", Path("d.md")
        )
        self.assertEqual(blocks[0].body, ("echo a", "", "echo b"))
        self.assertEqual(blocks[0].body_indent, 2)

    def test_body_less_indented_than_its_fence_is_not_over_stripped(self) -> None:
        """The dedent is the body's own common prefix, capped by the fence's indent."""
        blocks = sut.extract_blocks(
            "   ```bash\n echo a\n echo b\n   ```\n", Path("d.md")
        )
        self.assertEqual(blocks[0].body, ("echo a", "echo b"))
        self.assertEqual(blocks[0].body_indent, 1)


class InfoStringTests(unittest.TestCase):
    """CommonMark's language is the info string's FIRST word, compared without case.

    Matching the whole info string exactly skipped ```` ```bash title="x" ```` and
    ```` ```Bash ```` silently: the file was still counted, so the run read as clean.
    """

    def test_attributes_after_the_language_still_open_a_block(self) -> None:
        self.assertEqual(
            sut.fence_language('```bash title="install"', ("bash", "sh")), "bash"
        )

    def test_the_language_is_matched_without_case(self) -> None:
        self.assertEqual(sut.fence_language("```Bash", ("bash", "sh")), "bash")

    def test_a_capitalized_lang_option_matches_a_lowercase_fence(self) -> None:
        self.assertEqual(sut.fence_language("```bash", ("BASH",)), "bash")

    def test_the_script_shebang_uses_the_folded_language(self) -> None:
        block = sut.extract_blocks("```SH\necho hi\n```\n", Path("d.md"))[0]
        self.assertEqual(sut.block_to_script(block), "#!/usr/bin/env sh\necho hi\n")

    def test_a_language_word_that_merely_starts_with_bash_is_not_bash(self) -> None:
        self.assertIsNone(sut.fence_language("```bashrc", ("bash",)))


class TildeFenceTests(unittest.TestCase):
    """CommonMark's tilde fences: a closer must use the opener's character."""

    def test_a_tilde_fence_opens_a_block(self) -> None:
        blocks = sut.extract_blocks("~~~bash\necho hi\n~~~\n", Path("d.md"))
        self.assertEqual([b.body for b in blocks], [("echo hi",)])

    def test_a_backtick_run_does_not_close_a_tilde_fence(self) -> None:
        blocks = sut.extract_blocks("~~~bash\necho a\n```\necho b\n~~~\n", Path("d.md"))
        self.assertEqual(blocks[0].body, ("echo a", "```", "echo b"))

    def test_a_tilde_run_does_not_close_a_backtick_fence(self) -> None:
        blocks = sut.extract_blocks("```bash\necho a\n~~~\necho b\n```\n", Path("d.md"))
        self.assertEqual(blocks[0].body, ("echo a", "~~~", "echo b"))

    def test_a_shorter_tilde_run_does_not_close_a_longer_one(self) -> None:
        blocks = sut.extract_blocks("~~~~sh\necho a\n~~~\n~~~~\n", Path("d.md"))
        self.assertEqual(blocks[0].body, ("echo a", "~~~"))

    def test_a_tilde_info_string_may_hold_a_backtick(self) -> None:
        self.assertEqual(sut.fence_language("~~~bash `x`", ("bash",)), "bash")

    def test_a_backtick_info_string_may_not(self) -> None:
        self.assertIsNone(sut.fence_language("```bash `x`", ("bash",)))


class LineSplittingTests(unittest.TestCase):
    """Only a newline ends a line: str.splitlines() also splits on form feeds and more.

    A form feed or U+2028 inside a block made splitlines() count an extra line, so every
    finding after it was reported one line too low.
    """

    def test_a_form_feed_does_not_shift_the_following_block(self) -> None:
        text = "intro \x0c more\n\n```bash\necho hi\n```\n"
        self.assertEqual(sut.extract_blocks(text, Path("d.md"))[0].start_line, 3)

    def test_a_line_separator_stays_inside_its_body_line(self) -> None:
        text = "```bash\necho a b\necho c\n```\n"
        self.assertEqual(
            sut.extract_blocks(text, Path("d.md"))[0].body, ("echo a b", "echo c")
        )

    def test_a_text_without_a_final_newline_keeps_its_last_line(self) -> None:
        blocks = sut.extract_blocks("```bash\necho a", Path("d.md"))
        self.assertEqual(blocks[0].body, ("echo a",))
