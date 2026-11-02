#!/usr/bin/env python3
"""A tiny fake CLI used to hand-verify cli_doc_consistency.

It deliberately carries two drifts so a by-hand run shows real catches:

* ``--count`` hardcodes ``(default: 5)`` in its help text (Check A).
* ``--mode`` exists here but is missing from ``widget.md``'s table (Check B).

This is a fixture, not a real tool. It is excluded from the lint / gate walks
via ``[tool.*] exclude`` in the repository's ``pyproject.toml``.
"""

from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    """Build the fixture's argument parser."""
    parser = argparse.ArgumentParser(
        description="Frob a widget (sample fixture).",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--input", required=True, help="Widget file to frob.")
    parser.add_argument(
        "--count",
        type=int,
        default=5,
        # Drift on purpose: the literal "(default: 5)" double-prints under
        # ArgumentDefaultsHelpFormatter, which is exactly what Check A flags.
        help="Number of times to frob (default: 5).",
    )
    parser.add_argument("--verbose", action="store_true", help="Chatty output.")
    # Drift on purpose: this option is absent from widget.md's table (Check B).
    parser.add_argument("--mode", default="fast", help="Frob mode.")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse args and echo them (the fixture does no real work)."""
    args = build_parser().parse_args(argv)
    print(f"frobbing {args.input} x{args.count} mode={args.mode}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
