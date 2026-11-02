# shellcheck_md

Lint the shell snippets embedded in Markdown. The tool extracts every fenced
bash/sh block (indented ones included; see [What counts as a
block](#what-counts-as-a-block)), runs `shellcheck` over each (with a synthesized
shebang for dialect detection), and reports any finding remapped to its original
`file:line:column`. Exit status is non-zero when shellcheck reports anything, so it
can fail a build or a commit beside the other linters.

## What it does

- Scans Markdown files under a root directory (skipping `node_modules`, virtualenvs and
  cache/VCS dirs, since a node project's dependency READMEs are not ours to lint), or
  exactly the files named with `--files`, as a pre-commit hook passes its staged files.
- Pulls out fenced `bash`/`sh` code blocks (configurable via `--lang`).
- Runs `shellcheck --format=gcc` over the extracted blocks, split across as many
  invocations as the command-line ceiling requires (see below), with a copy of the
  `.shellcheckrc` nearest each Markdown file beside the scripts lifted from it, and merges
  the findings.
- Reads `shellcheck`'s exit status beside its output, so a run that reached no verdict
  is exit `2`, never a clean pass (see [ShellCheck's exit
  status](#shellchecks-exit-status)).
- Maps each finding back to its source Markdown line and column, and prints the
  findings sorted by file, line and column. Every printed line is escaped first (see
  [Text the tool did not write](#text-the-tool-did-not-write)).

## What counts as a block

The extractor matches a fence by shape, never by column, and this section is the
whole of its scope: what is in, what is deliberately out, and why.

**In scope.** A fence of three or more backticks, or of three or more tildes, whose language
is one of `--lang`'s values. The language is the info string's first word, as CommonMark
reads it, compared without case: ```` ```bash title="install" ```` and ```` ```Bash ```` are
both bash fences, and the script's shebang uses the lower-case word. A backtick fence's info
string may not hold a backtick (CommonMark reads that line as an inline code span); a tilde
fence's may. A byte-order mark at the start of a file is dropped before the scan, so it
cannot hide a fence on the first line. Indentation is accepted at any depth and measured,
because indenting a fence is the normal spelling of a code block nested under a list item,
and CommonMark allows up to three spaces even at the top level. A flat scanner cannot know
which container a line sits in, so it must choose between over- and under-accepting
indentation, and the two are not symmetric: over-accepting lints a block CommonMark would
call literal text, which surfaces as a spurious finding on the line that caused it, while
under-accepting is a silent miss: the file is still read and still counted, so the
`Checked N Markdown file(s)` line reads the same whether a block was linted or never seen.
Prefer the loud failure. (Measured on a repository of several hundred Markdown files:
over-acceptance produced zero false positives across all of them.)

The same choice covers a shell fence shown *inside* another fence, as a Markdown example
of a Markdown file is: the scanner follows only the fences it lints, so the inner
```` ```bash ```` opens a block and is linted. That over-accepts too, and surfaces the same
loud way.

**The closing fence is judged against its opener**, not found by a column-zero search. It
must use the opener's character and at least as many of it, carry nothing but whitespace
after them, and sit no more than three characters further in. That last bound is what
stops a deeply-indented run of backticks *inside* a block from ending it early.

**Lines end at a newline and nowhere else.** A form feed, NEL or U+2028 in a file does not
end a Markdown line, so it does not shift the line number of any finding after it.

**The body is dedented** by the opening fence's indentation, capped by the body's own
common leading whitespace. This is not tidiness: a heredoc terminator has to reach column
zero, so handing shellcheck Markdown's indentation turns a valid heredoc into an
unterminated one. The width removed is uniform per block and is added back to every
reported column, so `file:line:column` still points at the character that caused the
finding. At indent zero the dedent is a byte-exact no-op.

**Out of scope, deliberately.** *Inline code spans* (a command written between single
backticks in prose) are not linted and are not intended to be. A span is a fragment
inside a sentence, routinely a bare option or a path rather than a runnable command, and
feeding those to shellcheck would report on text no one can execute. *Fences inside a
blockquote* are not linted either: each of their lines starts with `>`, which no fence
pattern matches. Stripping quote markers would need the container tracking a flat scanner
does not have, so a shell example meant to be linted belongs outside a blockquote.

## Usage

```text
shellcheck_md.py --root DIR [--glob PATTERN] [--exclude PATH ...] [--shellcheck PATH]
                 [--timeout-seconds SECONDS] [--lang LANG ...]
shellcheck_md.py [--exclude PATH ...] [--shellcheck PATH] [--timeout-seconds SECONDS]
                 [--lang LANG ...] --files FILE ...
```

| Option | Required | Default | Meaning |
| --- | --- | --- | --- |
| `--root` | one of the two | none | Directory to search for Markdown files. |
| `--files` | one of the two | none | Markdown files to lint instead of searching `--root`; takes several. Must be last: every argument after it is a file name, never an option. |
| `--glob` | no | `**/*.md` | Glob, relative to `--root`, selecting files. A match that is a directory is skipped. |
| `--exclude` | no | none | File or directory to skip; repeatable. Use it for scratch / reference trees that should not gate (e.g. `vendor/`, `_scratch/`). |
| `--shellcheck` | no | see below | shellcheck executable: name on PATH or full path. |
| `--timeout-seconds` | no | `300.0` | Wall-clock bound on each `shellcheck` call; exceeding it is exit `2` (could-not-run), never a clean result. |
| `--lang` | no | `bash`, `sh` | Fence language to lint, matched without case; repeatable. Any value replaces the built-in `bash`, `sh` set. |

Exactly one of `--root` and `--files` is required: neither is the usage error "give --root
or --files", both is "give --root or --files, not both", and `--files` with nothing after it
is "argument --files: expected at least one argument" (each exit `2`). The two lines of the
synopsis are the two forms, and `--files` closes the second because every argument after it
is a file name (see [Named files](#named-files---files-and-pre-commit)). No option may be
abbreviated: `--shell` is refused rather than read as `--shellcheck`. The `--glob` and
`--timeout-seconds` defaults above are exactly what `--help` prints, rendered by the shared
`help_formatter.UnsetHidingFormatter` from the live `default=` values, so they cannot drift.
The options whose `default=` is `None` or an empty list print no default at all, because
neither is a value anyone could type; their help says what leaving them out means. `--lang`
is one: the built-in `bash`, `sh` set shown above is the `DEFAULT_LANGS` constant, applied
after parsing whenever `--lang` is omitted, and its help says "omitting it keeps that set".

**Which `shellcheck` runs.** `--shellcheck` names it. Left out, the tool first looks in
the running interpreter's own scripts directory (`bin`, or `Scripts` on Windows) for
`shellcheck` (`shellcheck.exe`), which is where the `shellcheck-py` package installs the
binary; a pipx install links only this tool's own command onto PATH, so that directory is
often not on it. Failing that, the bare name `shellcheck` is run from PATH.

**Prerequisite:** a `shellcheck` executable must be reachable, as above or via
`--shellcheck`. Run it from a project root, or from a pre-commit hook, beside the
other linters.

**Exit codes:** `0` clean · `1` shellcheck findings reported · `2` untrusted pyproject
config (`[tool.shellcheck_md]` malformed or unreadable), a `--files` entry that is not a
file, a Markdown file that cannot be read as UTF-8 text (every such file is named), a
temporary script, folder or rc-file copy that could not be written (named as a write, never
as a missing `shellcheck`), a symlinked rc file (refused, not followed), or `shellcheck`
could not be run or reached no verdict: not found, unable to start, outlasted
`--timeout-seconds`, or an exit status that carries no verdict (next section).

## ShellCheck's exit status

`shellcheck` exits `0` when it found nothing, `1` when it found something, `2` when some
files could not be processed, `3` for bad syntax or options and `4` for an unknown output
format. The tool reads that status beside the parsed output of each call, because
either alone can lie:

| Status | Output that maps back to a Markdown file | This tool |
| --- | --- | --- |
| `0` | nothing parsed as a finding | clean |
| `0` | any finding | exit `2`: status and output disagree |
| `1` | at least one finding | findings reported, exit `1` |
| `1` | none | exit `2`, quoting the output's tail |
| `2` | any | the findings are printed, then exit `2`: the run is incomplete |
| `3`, `4`, other, or a signal | any | exit `2`, quoting the output's tail |

A status of `2` does not stop the run: every batch still runs and every finding is
printed, and only then is the run reported as incomplete. Any other status that carries no
verdict abandons the remaining batches, as a timeout does; the findings earlier calls did
report are still printed before the error, which also quotes any earlier status-`2` output.
A timeout or a `shellcheck` that cannot be started prints only the error.

**`SHELLCHECK_OPTS` still applies, and is not stripped.** `shellcheck` reads default flags
from that environment variable, so a user's `-x`, `--severity` or `-e` reaches every call,
as it would reach a script linted by hand. The case it can break is the output format: set
to `-f json`, it makes `shellcheck` print JSON under a status of `1`, which no gcc-format
line parses from. That is the second `1` row above: exit `2`, naming `SHELLCHECK_OPTS` as
the likely cause, never the "0 finding(s)" a parse that ignored the status reported.

## ShellCheck rc files

`shellcheck` looks for `.shellcheckrc`, then `shellcheckrc`, in a script's directory and
each parent, and falls back to the user's own (`~/.shellcheckrc`, then the XDG or
`%APPDATA%` one). The extracted scripts live in a temporary directory, so that search
would never see a project's rc file. The tool therefore runs the same search itself from
each Markdown file's directory up to the filesystem root, groups the scripts by the rc
file found, writes each group to its own subfolder of the temporary directory, and copies
that rc file into it as `.shellcheckrc`. `shellcheck`'s own search then finds the copy
first. Scripts from a Markdown file with no rc file above it sit in a subfolder with none,
so the user's own rc file still applies to them, exactly as it would to a script with none.

The copy, rather than `--rcfile`, is deliberate: `--rcfile` arrived only in ShellCheck
0.10.0, and an older `shellcheck` refuses it as a bad option, while every version that reads
rc files at all searches the script's own folder.

**What the copy changes: relative paths in the rc file.** The rc file is read from the
temporary subfolder, so a relative `source-path=` in it resolves against that subfolder,
not against the project. `source-path=SCRIPTDIR` names the temporary subfolder too.

**Known limit: `source` resolves against the temporary directory.** A block that sources a
file by a relative path, such as `. <helper>.sh`, is checked as a script in the temporary
directory, so `shellcheck` cannot find the file and reports `SC1091` (not following), and
with `-x` it follows nothing from the project. A finding `shellcheck` reports on any file
other than an extracted script is not mapped back and is not printed; a status of `1` whose
findings all fall there is exit `2`. Disable `SC1091` in the project's rc file, or keep
sourced helpers out of linted examples.

**Known limit: an rc file can make `shellcheck` read other files.** The rc file is the
project's own, and the tool honors it as `shellcheck` would. One that sets
`external-sources=true` or a `source-path` lets `shellcheck` *read* the files a
block sources, wherever they are; it never runs them. Linting a repository you do not trust
means trusting its rc file to that extent.

**A symlinked rc file is refused.** Copying the rc file follows a symbolic link, so a
`.shellcheckrc` or `shellcheckrc` that is one would read whatever file it names (outside
the project, possibly) into the temporary directory. The tool refuses it instead: exit `2`,
`ERROR: refusing a symlinked ShellCheck rc file: <path>`, with the path escaped like every
other message. Skipping it would silently drop the project's directives, so the run stops
where it can be seen. Replace the link with a regular file.

## Named files: `--files`, and pre-commit

A pre-commit hook appends the staged filenames to the end of its command. `--files` takes
one or more values, so a hook whose command ends in `--files` hands every appended name to
it, and the tool lints exactly those files. It stays a named option, as every argument of
this tool is, since nothing about pre-commit needs a positional.

**`--files` must be last, and everything after it is a file name.** The tool splits its
command line at the first `--files` (or `--files=<name>`, whose value is the first file),
and only what comes before reaches the option parser. Without that split, a staged file
*named* like an option (`--shellcheck=<path>`, say) was parsed as that option, so a file
in the repository chose which executable ran as the backend; one that printed nothing and
exited `0` read as a clean pass. Now that name is just a file: linted if it exists, exit `2`
as "not a file" if not. A later `--files` or `--root` is a file name too.

**A hook that sets its own `args` must end them with `--files` too.** The shipped hook's
default `args` are just `--files`; a consumer's `args` override replaces them. Always end
your own `args` with `--files`; without it, a staged file named like an option would be read
as one, which is the very injection the split exists to close.

A named file is the caller's choice, so neither the directory-name prune nor the virtualenv
test applies to it, and `--glob` is ignored. The excludes still bind, as ruff's
`--force-exclude` does in a hook, so a standing exclude means the same in both modes: each
named file's `[tool.shellcheck_md] exclude` comes from the nearest `pyproject.toml` holding
that table (see [Configuration](#configuration)), since named files can sit under different
projects, unioned with `--exclude`. A file named twice (under two spellings, too, such as
`a.md` and `<dir>/../a.md`) is linted once, and reported under the spelling given first. A
symbolic link to a Markdown file is followed, in both modes: it is linted where it points,
and reported under the name given. A named path that is not a file is exit `2` before
anything is read: reading it would raise `FileNotFoundError`, the class a missing
`shellcheck` raises, and the tool would blame the backend.

## The time bound

`--timeout-seconds` sits on each `shellcheck` call itself, because an outer deadline can
only fire *between* calls and never interrupts the one already in flight. Since the subject
scripts are split into batches, a split run can take a multiple of the bound in total: one
per batch. `shellcheck` is a fast native binary (a whole tree of fences is seconds), so the
default is headroom for a cold filesystem rather than a tight SLA, and even several batches
of it stay inside any bound a build or a pre-commit run would put around the whole tool, so
a wedged `shellcheck` is named by this tool rather than killed generically from outside. A
timeout abandons the remaining batches rather than retrying them: a wedged backend is an
environment fault the next batch would only reproduce, at another full bound each.

## The command-line ceiling, and the misleading diagnostic it causes

Every extracted block becomes a temporary script, and every one of those paths travels to
`shellcheck` on the command line, which has a hard ceiling. A big enough tree crosses
it. Measured on one repository: 306 Markdown files yielded 391 blocks, whose single argv
rendered 33,967 characters against a 32,767 ceiling, over by 1,200, or about a dozen files.

Crossing it is far worse than a clear error. Windows reports `WinError 206`, and CPython
raises that as `FileNotFoundError`: the same class, and the same `errno` of 2, that a
genuinely absent executable raises. Only `winerror` separates them (206 =
`ERROR_FILENAME_EXCED_RANGE` against 2 = `ERROR_FILE_NOT_FOUND`). Reported through the
wrong branch, an overflow reads as *"could not run shellcheck executable 'shellcheck' (not
found on PATH or at that path)"*, naming a backend that is present and working, and sending
its reader to reinstall it.

Two defenses, both required:

- **The split.** The `argv_budget` library, in `argv_budget/` beside this folder, owns the
  ceilings, the `list2cmdline` measurement and the splitter; this tool contributes only its
  own argv head (see `build_shellcheck_args`, so the width charged to each batch is measured
  from the very list that is sent). Findings are accumulated per batch, and a path too wide
  to fit even alone is isolated rather than dropped, because a check that silently skips a
  file reports a clean run it never earned.
- **The diagnostic.** `could_not_run_message` asks `winerror` before choosing its wording,
  so the residual case names the real cause instead of the plausible one. It stays even
  though the split makes it near-unreachable, because being wrong here costs a reader far
  more than the branch costs to keep.

This tool does not resolve `--shellcheck` with `shutil.which` before asking which ceiling
binds. `shellcheck` is a real executable rather than an npm `.CMD` shim, and `CreateProcess`
only ever appends `.exe` to a bare name, so a shim could not be launched here at all, and
every reachable case is judged correctly from the value as given: a bare name gets the
32,767 ceiling it will actually run under, and an explicit `--shellcheck` path ending
`.cmd`/`.bat` gets cmd.exe's tighter 8,191.

## Example

A finding prints as `file:line:col: severity: message [code]`. The shipped
`samples/` directory demonstrates the remap; run from this folder, `shellcheck_md/`.

The default run lints only `bash`/`sh`. It scans both Markdown files under
`samples/`, `README.md` (only `text`-tagged fences, so it adds no findings)
and `embedded_shell.md` (one clean `bash` block), and exits 0:

```text
$ python shellcheck_md.py --root samples
Checked 2 Markdown file(s): 0 shellcheck finding(s).
```

Adding `--lang ksh` lints the sample's deliberately-dirty `ksh` block; the
`SC2086` finding is remapped from the temporary script back to the source
Markdown line, and the tool exits 1:

```text
$ python shellcheck_md.py --root samples --lang ksh
samples/embedded_shell.md:26:6: note: Double quote to prevent globbing and word splitting. [SC2086]
Checked 2 Markdown file(s): 1 shellcheck finding(s).
```

See `samples/README.md` for why the demonstration block is tagged `ksh`.

## Configuration

Standing excludes live in a `[tool.shellcheck_md]` table in the nearest `pyproject.toml`
that has one, looked up from each Markdown file. The rule is the same in both modes, so a
subproject's own excludes bind a `--root` run of the tree above it exactly as they bind a
pre-commit run on its files. A `pyproject.toml` without the table is passed over, as ruff
passes over one without `[tool.ruff]`, so a subproject's unrelated `pyproject.toml` cannot
cancel the excludes above it; a table with no `exclude` key does stop the search, meaning
"no standing excludes here". The `exclude` key is a list of strings (paths relative to the
`pyproject.toml` directory); they are resolved and unioned with any `--exclude` flags passed
on the command line. No table or no key means no standing excludes. A malformed
`pyproject.toml` (invalid TOML, a `[tool.shellcheck_md]` that is not a table, non-list
`exclude`, non-string entry) is exit 2: config that cannot be trusted must stop the gate,
not silently shrink it. With `--root`, the one nearest the root is read even when no file is
found under it. The table is the repository's own configuration, so an `exclude` in it can
hide that repository's own blocks from the run; that is by design, and an exclude that hides
every file says so (next section).

## When the excludes hide everything: `NOTHING EXAMINED`

A bare `Checked 0 Markdown file(s)` line and exit 0 cannot be told apart from a verified-clean
run, so an `exclude` entry broader than its author meant would report a clean gate, for a
tree nobody is checking, indefinitely. When the effective exclude set prunes every
candidate under `--root`, the report therefore says outright that it verified nothing, and
names how many Markdown files were hidden (with `--files`, the line says "under the named
files"):

```text
NOTHING EXAMINED: all 3 candidate Markdown file(s) under --root were pruned by the exclude set
([tool.shellcheck_md] exclude and/or --exclude), so this result verifies nothing. …
```

The exit status deliberately stays 0. Every standing exclusion is intentional, so failing
on one would break every build that runs the tool. The wording and the when-to-speak rule are
not this tool's own: they come from the shared `nothing_examined` library beside this folder.
See `../nothing_examined/nothing_examined.md` for the rationale.

## Text the tool did not write

A Markdown file's name, a finding's message and `shellcheck`'s own output come from outside
the tool, and any of them can hold an escape sequence that drives the terminal or CI log it
lands in, or a direction override that makes one name display as another. Every finding
line and every error line is therefore passed through `stdio_utf8.spell_unsafe` before it is
printed: C0 and C1 controls and DEL print as `\xNN`, and the direction marks and line and
paragraph separators as `\uXXXX`. The newlines the tool itself puts between the lines of a
message stay line breaks; a separator inside quoted `shellcheck` output does not start a
new line.

argparse's own usage and error text quotes the command line too ("unrecognized arguments:
…" can repeat a staged file's name), so the parser is built on
`stdio_utf8.EscapingArgumentParser`, with each line of its messages also passed through
`spell_unsafe`: the base class escapes only what UTF-8 cannot encode, not a terminal control.

## Notes

- A block with no closing fence (unterminated before EOF) is still captured, so
  the gate catches shell in malformed Markdown too.
- `--root`'s directory prune judges only the parts of a path below the root, so a root
  that itself sits inside a `node_modules` or `.venv` is still searched.
- Each temporary script is named for its serial number, block and line, with up to 64
  characters of the Markdown file's stem (characters outside `A-Z a-z 0-9 . _ -` become
  `_`), so a long or non-ASCII filename cannot push a script's name past the filesystem's
  limit.
- Only `bash`/`sh` fences are linted by default; ```` ```powershell ````,
  ```` ```text ```` and ```` ```console ```` transcripts are ignored. Tag an
  interactive transcript as ```` ```console ```` to keep it out of the gate.
- On Windows, pass `--shellcheck` a full path if the executable is not on PATH.
