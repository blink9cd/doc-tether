# argv_budget.py

Shared library (not a CLI) owning one measurement: **how wide is this command line going
to be, and where must the path list be split so it fits?** — with
the binding ceiling derived from what is actually being launched, not assumed.

## The problem it solves

A gate that discovers a file set hands it to its backend **on the command line**, and a
command line has a hard ceiling. Every such tool reaches it sooner or later, and one that
grows its own splitter each time ends up with several that disagree. This library is the
one splitter they share; `shellcheck_md` is its caller here.

Reaching the ceiling would be a small problem if it announced itself. It does not:

| How it surfaces | What the reader is told | What is actually wrong |
| --- | --- | --- |
| `CreateProcess` refuses past 32,767 chars | `WinError 206`, which Python raises as `FileNotFoundError` — so a handler reports "not found on PATH" | The executable is present and working; the argv is too long |
| a `.CMD` shim overflows cmd.exe's 8,191 | `The command line is too long.` and **exit 1** — the same code a linter uses for "findings reported" | Nothing was linted at all |

Both mislead in the same expensive direction: they read as an environment or install
fault, so the reader goes hunting a problem that does not exist. The measured case that
prompted this library was a staged tree of 325 Python files whose absolute paths averaged
about 101 characters — a 32,968-character argv against a 32,767 ceiling, over by 201.

## Why the resolved path, not the platform

The ceiling is not a property of the operating system alone. An npm-installed tool on
Windows is a **`.CMD` batch shim**, and running one does not reach the shim directly:
`CreateProcess` wraps it as `%COMSPEC% /c "<argv>"`, so **cmd.exe** parses the line and
*its* 8,191-character limit binds — a quarter of what a caller budgeting for
`CreateProcess` would assume.

So `argv_limit_for` judges by the **resolved** path's suffix. Two consequences:

- **Resolve before asking.** A bare name such as `markdownlint-cli2` carries no suffix to
  judge by and falls through to the looser ceiling, which is exactly the wrong answer for
  an npm install. Run the tool's own executable-resolution step first.
- **The suffix decides, not the host.** A `.cmd` suffix picks 8,191 on any platform. That
  keeps the verdict a function of its input, and keeps this path exercised where it is
  easy to test rather than only where it is hard.

The budget itself applies on every platform rather than behind a Windows branch. POSIX
`ARG_MAX` is far larger, so a per-OS budget would leave the tight case unexercised on the
machines most likely to run the tests, and a green run there would prove nothing about the
tightest target.

## What is deliberately not here

Only the **measuring** and the **splitting** moved. Three things stayed with each backend,
because each is a fact about that backend rather than about command lines:

- **Its argv prefix.** Which subcommand and which flags precede the paths. The caller
  measures its own head and passes the width, so this library never has to know that
  `markdownlint-cli2` wants a no-globs flag or that `ruff` wants a JSON output format.
- **Its path token.** `markdownlint-cli2` needs a `:`-prefixed POSIX literal path; `ruff`
  takes the bare path. That is why `batch_paths` accepts a `render` callable — measuring
  the bare path while emitting the prefixed one would size every batch against a shorter
  line than the one actually built.
- **What its exit codes mean.** The rule that tells a real "findings reported" exit 1 from
  a `.CMD` overflow that also exits 1 is that backend's vocabulary, and stays with its
  caller.

**The two headrooms are also deliberately two numbers.** `DEFAULT_HEADROOM` is 512, sized
for the one wrapper that can be reasoned about (`%COMSPEC% /c`, whose width varies only
with the value of `COMSPEC`). A caller launching through a wrapper it does not build and
cannot measure at all passes a larger reserve, 2,048 say. One value for both would change
one caller's batch sizes in exchange for tidiness, so the library owns the default and a
caller needing a different reserve says so.

## API

| Function | Returns |
| --- | --- |
| `rendered_length(args)` | Width of *args* as one command line, its quoting included |
| `argv_limit_for(executable)` | The ceiling that binds for *executable* — 8,191 or 32,767 |
| `batch_paths(paths, *, prefix_chars, limit, headroom, render)` | *paths* split into batches whose argv fits `limit - headroom - prefix_chars` |

| Constant | Value | Meaning |
| --- | --- | --- |
| `CMD_SHIM_ARGV_LIMIT` | 8191 | cmd.exe's ceiling — a `.CMD`/`.BAT` shim |
| `CREATE_PROCESS_ARGV_LIMIT` | 32767 | CreateProcess's ceiling — a real executable |
| `DEFAULT_HEADROOM` | 512 | Reserve for the `%COMSPEC% /c` wrapper |

Every function is pure: no filesystem access, no subprocess, no knowledge of any
particular gate.

Two behaviors are contracts rather than incidental:

- **Widths come from `subprocess.list2cmdline`, never a raw character count.** That is the
  rendering the subprocess layer actually builds, so a path containing a space is measured
  *with* the two quotes it acquires. A raw length under-reports every such token, and a
  batch sized that way overflows the very ceiling it was sized against. Pinned by a test:
  40 quoted paths against a 400-character budget rendered 419 before this was fixed.
- **A path too wide to fit even alone still gets its own batch.** It is never dropped. A
  gate that silently skips a file reports a clean run it never earned, which is the one
  outcome worse than failing on it — so an argv that genuinely cannot be built surfaces as
  the backend's own failure instead.

## Usage

Import it the way the tools here import `stdio_utf8` — a `sys.path` bootstrap resolved from
the importing file's own location, so the tool stays location-independent:

```python
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "argv_budget"))

import argv_budget  # noqa: E402 - import follows the sys.path bootstrap above
```

Then, once the executable is resolved and the argv head is known:

```python
head = [*ruff_cmd, "format", "--check"]
for batch in argv_budget.batch_paths(
    paths,
    prefix_chars=argv_budget.rendered_length(head),
    limit=argv_budget.argv_limit_for(head[0]),
    headroom=ARGV_HEADROOM,
):
    result = run([*head, *(str(p) for p in batch)])
```

Two things the caller still owns. Accumulate results across batches, so a finding in a
later run is not lost — and apply any per-batch classification *per batch* rather than to
the combined result, or one batch's findings can mask another batch's death. Any per-call
timeout also bounds **each** run rather than the whole set, so a list split three ways can
take up to three times that bound in total.

## Worked example

325 files whose paths render 95 characters each, against a 40-character argv head. The
same list splits differently depending only on what is being launched:

```text
single argv would render: 31219 chars vs ceiling 32767
ruff.exe  limit=32767  batches= 2  sizes=[319, 6]
ruff.CMD  limit= 8191  batches= 6  sizes=[63, 63, 63, 63, 63, 10]
```

The first line is the near miss worth noticing: at 95-character paths this list fits in one
argv, and at the ~101 characters the real staged tree carried it did not. The headroom
between "works when I run it by hand" and "fails in the build" was about four files.

## Tests

```bash
python argv_budget/test_argv_budget.py
```
