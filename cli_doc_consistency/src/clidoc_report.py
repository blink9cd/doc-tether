"""Render the verdict: the human report, the JSON report, and the PASS/FAIL line.

Private to ``cli_doc_consistency`` (it lives in that folder's ``src/``); see
``clidoc_model`` for why every module here carries the tool's prefix.

Only *gating* findings decide the verdict; advisory ones are printed and then ignored by
the arithmetic. That split is the reason this module exists separately from the checks:
each check decides a severity for its own finding, and exactly one place decides what a
collection of severities means for the exit code.

The verdict prefix is derived from the program name rather than written as a literal, so
renaming the tool cannot leave the banner lying.

Entry point assumptions
-----------------------
Returns text; it does not print. Callers own the streams, which is what keeps the report
testable without capturing process output. No filesystem reads, no subprocesses. The one
sibling library it needs is bootstrapped here rather than inherited from the parent's
``sys.path`` inserts, so importing this module on its own needs no import order.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from clidoc_model import SEVERITY_ADVISORY, SEVERITY_GATING, Finding

# nothing_examined is the shared library owning the one wording for "the exclude set
# pruned every candidate, so this result verifies nothing" -- a sibling per-tool folder,
# resolved from this file's own location two levels up.
sys.path.insert(
    0, str(Path(__file__).resolve().parent.parent.parent / "nothing_examined")
)

import nothing_examined  # noqa: E402 - import follows the sys.path bootstrap above

# stdio_utf8 owns spell_unsafe, the one escaping of characters a terminal would act on.
# Every file name, option literal and message the scanned tree controls goes through it
# in the text report, so a name holding an ESC or a newline cannot repaint the terminal
# or print a forged verdict line. The JSON report needs nothing: json.dumps escapes them.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "stdio_utf8"))

import stdio_utf8  # noqa: E402 - import follows the sys.path bootstrap above

_safe = stdio_utf8.spell_unsafe


def _verdict_name(prog: str) -> str:
    """Derive the verdict prefix from the program name, never a hardcoded literal."""
    return Path(prog).stem.upper().replace("-", "_")


def _format_finding(finding: Finding) -> str:
    """Render one finding as a location line plus an indented fix line."""
    location = _safe(
        f"{finding.file}:{finding.line}" if finding.line is not None else finding.file
    )
    head = (
        f"  {location}  [{finding.check} {finding.severity}] {_safe(finding.context)}"
        f"  - {_safe(finding.message)}"
    )
    return f"{head}\n      fix: {_safe(finding.fix)}"


# The caveat for a run that found no CLI at all, with nothing excluded: a folder with no
# argparse CLI is a normal subject for a whole-tree run, so the verdict stays PASS, but
# the report must not let "Checked 0" read as a verified-clean tree.
_NO_CLI_CAVEAT = (
    "NOTHING EXAMINED: no argparse CLI file (a .py that constructs an ArgumentParser) "
    "was found under --path, so this result verifies nothing."
)


def _caveat(results: dict[Path, list[Finding]], hidden: int) -> str | None:
    """The nothing-examined line for a run that checked no CLI, else None."""
    if results:
        return None
    return (
        nothing_examined.note(hidden, noun="CLI file", table="cli_doc_consistency")
        or _NO_CLI_CAVEAT
    )


def _render_json_report(
    results: dict[Path, list[Finding]],
    gating: int,
    advisory: int,
    caveat: str | None,
) -> str:
    """Render the machine-readable report."""
    return json.dumps(
        {
            "files": {
                str(path): [
                    {
                        "check": f.check,
                        "severity": f.severity,
                        "line": f.line,
                        "context": f.context,
                        "message": f.message,
                        "fix": f.fix,
                    }
                    for f in file_findings
                ]
                for path, file_findings in results.items()
            },
            "gating": gating,
            "advisory": advisory,
            "passed": gating == 0,
            "examined": len(results),
            "caveat": caveat,
        },
        indent=2,
    )


def format_report(
    results: dict[Path, list[Finding]],
    prog: str,
    *,
    as_json: bool,
    hidden: int = 0,
) -> str:
    """Render the report; only gating findings drive the PASS/FAIL verdict."""
    every = [f for file_findings in results.values() for f in file_findings]
    gating = [f for f in every if f.severity == SEVERITY_GATING]
    advisory = [f for f in every if f.severity == SEVERITY_ADVISORY]
    caveat = _caveat(results, hidden)
    if as_json:
        return _render_json_report(results, len(gating), len(advisory), caveat)
    lines: list[str] = []
    for path, file_findings in results.items():
        file_gating = [f for f in file_findings if f.severity == SEVERITY_GATING]
        if file_gating:
            lines.append(f"===== {_safe(str(path))} =====")
            lines.extend(_format_finding(f) for f in file_gating)
    if advisory:
        lines.append("Advisory (non-gating):")
        lines.extend(_format_finding(f) for f in advisory)
    if caveat is not None:
        lines.append(caveat)
    lines.append(
        f"Checked {len(results)} CLI file(s): {len(gating)} gating finding(s), "
        f"{len(advisory)} advisory."
    )
    lines.append(
        f"{_verdict_name(prog)} FINAL RESULT: {'PASS' if not gating else 'FAIL'}"
    )
    return "\n".join(lines)
