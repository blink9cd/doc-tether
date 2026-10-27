#!/usr/bin/env python3
"""A tiny fake CLI documented the README way, to hand-verify cli_doc_consistency's --doc.

Its doc is ``README.md`` beside it (not a sibling ``readme_tool.md``), which carries the
tool's pasted ``--help`` output in a fenced block. That block drifts on purpose:

* ``--format`` exists here but is missing from the pasted block (Check B coverage, found
  from the AST without running anything).
* the block says ``--limit`` defaults to 10; the code says 20 (Check B defaults, found
  only with ``--run-help``, which executes this file).

This is a fixture, not a real tool -- it is excluded from the lint / gate walks via
``[tool.*] exclude`` in the repository's ``pyproject.toml``.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the fixture's argument parser."""
    parser = argparse.ArgumentParser(
        description="List a catalog's entries (sample fixture).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--catalog", required=True, help="Catalog file to read.")
    parser.add_argument("--limit", type=int, default=20, help="Entries to show.")
    # Drift on purpose: this option is absent from README.md's pasted --help block.
    parser.add_argument("--format", default="text", help="Output format.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args and echo them (the fixture does no real work)."""
    args = build_parser().parse_args(argv)
    print(f"catalog={args.catalog} limit={args.limit} format={args.format}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
