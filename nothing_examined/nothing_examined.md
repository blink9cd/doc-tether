# nothing_examined.py

Shared library (not a CLI) giving every tool that uses it one way to say **"this run examined
nothing, because the excludes hid everything"** — the false clean that a bare
`Checked 0 …` line and exit status 0 cannot be told apart from.

## The problem it solves

A discovery-based check that finds no work prints a zero and exits 0, as `shellcheck_md`
does:

```text
Checked 0 Markdown file(s): 0 shellcheck finding(s).
```

That is produced by two very different situations:

| Situation | What it means | Can it be a mistake? |
| --- | --- | --- |
| the tree holds no file of that type | a fact about the tree | no |
| every candidate was pruned by the exclude set | a fact about the **configuration** | **yes** |

Only the second can be wrong, and it is the one that hides. An `exclude` entry broader
than its author meant — a bare `docs` where one file was intended — reports a clean gate
for a tree nobody is checking, indefinitely, and the broader the entry the quieter the
failure. This module makes the second case say so, in the same words everywhere.

## It deliberately does not fail the build

Every standing exclusion in a mature tree is intentional: a `samples/` fixture tree holding
a deliberate defect for hand-verification, a vendored third-party tree that is not the
project's to lint. Failing on those would break every build that runs the check and train
the reader to ignore it.
The line addresses the risk by being **legible**, not by being fatal.

## API

Both functions are pure — no I/O, no filesystem access, no knowledge of any particular
gate. The tool keeps sole ownership of *how* it discovers and prunes; this module owns only
the rule for *when* to speak and the words to use.

| Function | Returns |
| --- | --- |
| `hidden_count(examined, count_all)` | how many candidates the excludes hid, or `0` when anything was examined |
| `note(hidden, *, noun, table, flag="--path")` | the `NOTHING EXAMINED` line, or `None` when nothing was hidden |

`count_all` must re-run **the caller's own discovery** with its *configured* excludes off —
the ones `note` blames — while keeping any prune that reflects what was never a candidate for
those to hide. A wholly empty exclude set is the wrong baseline wherever a gate prunes on some
other authority's answer: it counts those paths as concealed and pins them on a config table
that never touched them. Passing the real discovery function rather than a reimplemented walk
is the point: the count then cannot drift from the prune rules it reports on. It is called
only when `examined` is zero, so the common path never walks the tree twice.

`note` returns `None` rather than `""` so a caller building a list of report lines can
filter falsy values without emitting a blank line. `noun` is the singular unit the gate
counts (`file`, `doc`, `unit`); `table` is the tool's `[tool.<name>]` config table; `flag`
names the tool's own target option, so the line cites the argument the reader actually
passed (`--root` for `shellcheck_md`, or `--folder` for a gate that takes that).

## Usage

Import it the way the tools here import `stdio_utf8` — a `sys.path` bootstrap resolved from
the importing file's own location, so the tool stays location-independent:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "nothing_examined"))

import nothing_examined  # noqa: E402 - import follows the sys.path bootstrap above
```

Then, right after discovery, and printed above the tool's own count line:

```python
files = discover(path, excludes)
hidden = nothing_examined.hidden_count(len(files), lambda: len(discover(path, ())))
...
if line := nothing_examined.note(
    hidden, noun="Markdown file", table="shellcheck_md", flag="--root"
):
    lines.append(line)
```

## Worked example

`shellcheck_md` run with an exclude that covers every Markdown file under its root (the
first line is wrapped here; the tool prints it as one line):

```text
NOTHING EXAMINED: all 2 candidate Markdown file(s) under --root were pruned by the exclude set
([tool.shellcheck_md] exclude and/or --exclude), so this result verifies nothing.
Intentional for a deliberately-excluded tree; a surprise means the exclude is broader
than intended.
Checked 0 Markdown file(s): 0 shellcheck finding(s).
```

The exit status is still 0.

## Tests

```text
python nothing_examined/test_nothing_examined.py
```
