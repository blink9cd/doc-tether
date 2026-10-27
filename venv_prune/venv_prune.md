# venv_prune.py

Shared library (not a CLI) owning one question: **is this directory a Python virtual
environment?** — answered from the PEP 405 `pyvenv.cfg`
marker rather than from the directory's name.

## The problem it solves

A discovery-based gate prunes a set of directory *names* it never descends into:
`.git`, `node_modules`, `__pycache__`, the tool caches, and `.venv`. A name list only
knows the names someone thought of.

A virtualenv created under any other name, at the root a user then points the gate at —
the natural place to point it — turns into a flood of findings about third-party package
code.

Nothing has leaked and nothing is misconfigured. The walk simply descended into a tree
that was never ours to check. The failure is loud, but it reads like the gate is broken.

## Why the marker, not the name

Widening the name list fixes one name and leaves the class open, because the name is a
**proxy**. An adopter who runs `python -m venv venv`, or keeps a virtualenv named for the
Python release that built it, breaks in exactly the same way.

PEP 405 defines a virtual environment as a directory containing `pyvenv.cfg`, and CPython
computes `sys.prefix` by locating precisely that file. Keying on the marker makes the
verdict independent of the name, so every venv prunes for the same reason: state is read
from its authoritative source, not from a proxy.

Two consequences worth stating plainly:

- **It is additive.** Each tool keeps its existing name set. A half-built or
  partially-deleted venv whose `pyvenv.cfg` was never written still must not be walked,
  and the name list is what catches that.
- **It does not parse the marker.** Presence is the contract. PEP 405 mandates no
  particular key and tools disagree about what they write, so a stricter check could only
  produce false negatives — a real venv the gate then walks.

## API

| Function | Returns |
| --- | --- |
| `is_virtualenv(path)` | `True` when *path* holds a `pyvenv.cfg` **file**, else `False` |
| `is_under_virtualenv(path, root)` | `True` when any ancestor of *path* below *root* is a virtualenv |

Call `is_virtualenv` once per directory at a walk's prune step, on the directory itself
rather than on its children, so a venv root is never entered and nothing beneath it is
visited. Both are pure queries — they read paths and write nothing.

`is_under_virtualenv` is for the minority of gates that **glob and then filter** rather
than walking, such as `shellcheck_md`. Those have no prune step at which
to stop descending, so they must ask about the whole ancestor chain instead. Prefer
`is_virtualenv` at a walk wherever you have the choice: pruning avoids visiting the tree
at all, while filtering pays for every path it then discards.

*root* is **exclusive** and bounds the search. A marker on *root* itself is ignored,
because the caller deliberately chose to scan it and one stray file must not blank the
whole run; stopping there also keeps the walk from escaping upward into directories the
caller never asked about.

A path that cannot be examined — missing, permission-denied, or not a directory — reads
as *not* a virtualenv. That direction is deliberate for a gate: a wrong `False` costs a
scan of a tree that turns out to be third-party, while a wrong `True` silently skips real
source and reports a clean run for code nobody checked. `Path.is_file` already collapses
those OSError cases to `False`, so the safe default needs no extra handling.

## Usage

Import it the way the tools here import `stdio_utf8` — a `sys.path` bootstrap resolved from
the importing file's own location, so the tool stays location-independent:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "venv_prune"))

import venv_prune  # noqa: E402 - import follows the sys.path bootstrap above
```

Then at the existing prune step, alongside the tool's own name set:

```python
dirnames[:] = [
    d
    for d in dirnames
    if d not in _DEFAULT_EXCLUDE_DIRS and not venv_prune.is_virtualenv(here / d)
]
```

## Worked example

A directory is judged by what it contains, not what it is called:

```text
.tool-env/pyvenv.cfg  present   -> is_virtualenv(".tool-env") == True
venv/pyvenv.cfg       present   -> is_virtualenv("venv")      == True
src/                  no marker -> is_virtualenv("src")       == False
```

## Tests

```text
python venv_prune/test_venv_prune.py
```
