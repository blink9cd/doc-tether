# cli_doc_consistency.py

Catch doc-vs-code drift in Python `argparse` CLI tools, deterministically -- the
drift class that lint, tests, and coverage do **not** catch. Point it at a `.py`
file or a directory (`--path`); it discovers CLI files (a `.py` that constructs
an `argparse.ArgumentParser`, or a subclass whose name ends with it, such as
`stdio_utf8.EscapingArgumentParser`), pairs each with its sibling `<name>.md` (or,
for a single-file `--path`, the doc `--doc` names), and runs the checks below. Exit
`0` (clean) / `1` (a gating finding) / `2` (could not run). What is guaranteed: a
gating finding is never reported as a pass, a target it cannot read is exit `2` rather
than a skipped check, and a run that examined no CLI says `NOTHING EXAMINED` instead of
passing silently. What is not: Check B is deliberately conservative (below), so a drift
it cannot read with confidence is advisory, not gating.

**Prerequisites:** Python 3.11+ (its config reader imports the stdlib `tomllib`); no
third-party packages.

**Security: it can execute the code it checks, and the scanned tree can ask it to.**
Its checks read source with the AST. `--run-help`, or `run_help = true` in the config,
adds one step that **runs `<python> <tool> --help`** for each discovered CLI, with the
tool's folder as the working directory, to read the live defaults; that executes the
tool's module-level code and whatever its `--help` path runs. The config is the nearest
`pyproject.toml` at or above `--path`, which normally sits **inside the tree being
scanned**, so a tree you did not write can turn execution on by itself. **To scan a tree
you do not trust, pass `--no-run-help`**: it beats both `--run-help` and the config key,
and with it nothing under `--path` is executed.

## The four checks

| Check | What it flags | Gates? |
| --- | --- | --- |
| A -- hardcoded default | a `help=` string / f-string literal that contains `(default` but no `%(default)` token | yes |
| B -- .md mirrors the CLI | an option in the parser but missing from the doc's tables and `--help` blocks (or vice-versa); a missing companion doc; with `--run-help`, a purely-numeric default that clearly contradicts | yes (coverage, no option surface, missing doc, numeric default); advisory otherwise |
| C -- stated floor vs imports | a stated `3.x+` floor lower than a version-gated stdlib import requires | yes |
| D -- positional argument | an `add_argument` whose first arg is a string literal not starting with `-` (a positional, not a named option) | yes, when `--check-positionals` turns it on |

### Check A -- hardcoded default (AST only)

`ArgumentDefaultsHelpFormatter` appends its own `(default: X)` to each optional's
help. A hand-written `(default: ...)` in the same help therefore **double-prints**
and drifts from the real default. Check A walks every `add_argument(...)` and
flags a `help=` **literal** (a plain string or an f-string) whose text contains
`(default` (case-insensitive) but no `%(default)` token. Help built from a bare
variable, a call, or a concatenation is non-literal and is skipped, not guessed.

### Check B -- the companion `.md` mirrors the CLI

Check B is deliberately **conservative**: it hard-fails only on an *unambiguous*
mismatch, and reports everything it cannot parse with confidence as advisory
(which never gates). It uses the AST, and with `--run-help` a live `--help` render:

- The **option set** is taken from the AST (`add_argument` long options), which
  is complete and always available -- this is authoritative for the two coverage
  directions, and needs nothing executed, so coverage always runs and always gates.
  Every long spelling of an option counts: `add_argument("--dry-run", "--dryrun")`
  is covered by a doc naming either, and a doc naming either names a defined option.
  An `action=argparse.BooleanOptionalAction` option also answers to `--no-<name>`.
  **Positional arguments and short-only options (`-v` with no `--` spelling) are not
  compared at all**: Check B has no long name to match them by. Check D is what
  flags positionals, when turned on.
- The **rendered default values** (Check B's numeric-default comparison) come from a
  live `<python> <tool> --help` render, which runs only with `--run-help`. If the render
  fails (a missing import, a non-zero exit, a timeout), the source's literal defaults
  are used instead, and **one** advisory per file says so, with the exit status and the
  first line of the tool's stderr. **Without `--run-help`, no default is compared**:
  a doc that documents any default (a `Default` column, or a `(default: X)` in a
  pasted help block) gets **one** advisory per file saying the defaults were not
  checked and that `--run-help` executes the target to check them.
- The doc side is the **union** of two shapes, so a doc may use either or both:
  - **arguments tables**: every Markdown table whose first header cell reads
    `Argument`, `Option`, or `Flag`; option names are the `--tokens` in each row's
    first cell (`--[no-]fancy` names both `--fancy` and `--no-fancy`), and an optional
    `Default` column is read;
  - **pasted `--help` blocks**: every fenced code block (backtick or tilde fence, any
    info string) whose first non-blank line starts with `usage:` (any case), or is a
    prompt line ending in `--help` or `-h` (`$ tool --help`, an optional prompt
    character first) followed by a `usage:` line, which is how most READMEs document a
    CLI. Option entries are read **only inside an option section**: one opens at a
    column-0 heading whose title ends in `arguments`, `options` or `flags` (argparse's
    own `options:` / `optional arguments:` / `positional arguments:`), or at a heading
    `<title>:` for any argument-group title the CLI declares as a string literal
    (`add_argument_group("output")` or `title="output"`; compared case-insensitively),
    and closes at the next column-0 line. So an indented example under an epilog
    heading such as `examples:` is never read as an option. A group whose title is
    computed (a variable, an f-string) cannot be known without running the CLI, so its
    section is not read and its options are reported as undocumented: give it a
    literal title, or document those options in a table. Each
    option entry (a line in a section indented exactly two spaces that starts with `-`;
    `--opt=VALUE` reads as `--opt`) yields one name, the first long option in its option
    column -- exactly the name the AST gives the option, because argparse renders
    option strings in declaration order. A `(default: X)` in the entry is read as its
    documented default, by the same parser that reads the live render.

  An `## Exit codes` section is read when present.

**Where options are discovered, and the one boundary they cannot cross.** A file is a
subject when it **constructs** a parser, not when it calls `add_argument` or
`parse_args`, and each subject is paired with its own sibling `.md`. Its options are then
found by walking the **whole module** (`_iter_add_argument_calls` in
`src/clidoc_astcli.py` walks the module's syntax tree, not `build_parser`'s body), so an
option-adder helper defined in the **same file** and called from `build_parser` is
discovered, and extracting such a helper within the entry point is safe. What the walk
cannot cross is the **module** boundary: it follows no call into another file, so moving
an option adder to a second module takes those options out of the entry point's view,
and if that module constructs no parser it is not a subject at all.

**Subparser (subcommand) tools.** A tool built with `add_subparsers` documents its
options across one arguments table per subcommand (`### setup arguments`,
`### create-user arguments`, ...). Check B matches each CLI option against the
**union of all** arguments tables in the doc, so an option documented under its own
subcommand is not falsely flagged, and the same option legitimately appearing under
several subcommands (e.g. `--pat`, `--server-url`) is accepted.

Gating (hard-fail) findings:

- **no companion `.md`** beside the CLI -- a discovered argparse CLI must ship
  its doc (pass `--allow-missing-doc` to downgrade this one finding to advisory, or
  name a doc of another name with `--doc` when `--path` is a single file);
- an option present in the parser but **absent** from every arguments table and
  `--help` block in the doc;
- an option **documented** in a table or block that the parser does not define;
- a doc with **neither** an arguments table **nor** a pasted `--help` block: coverage
  would verify nothing, so the doc must carry one of the two shapes;
- with `--run-help`, a **purely-numeric** documented default that clearly
  contradicts the actual default (the actual value comes from the `--help` render,
  else the AST literal; both sides must parse as a bare number, so a paraphrased
  default cannot trip it). The default check runs only when an option's default is
  documented in **exactly one** table or block -- an option repeated across
  subcommand tables has no single documented default, so its default is left
  unchecked rather than guessed.

Advisory (reported, never gates) findings:

- documented defaults left unchecked because `--run-help` is off (one per file);
- with `--run-help`, a live render that failed, so defaults were compared against the
  source's literals (one per file);
- a missing companion `.md` **when `--allow-missing-doc` is passed** (otherwise it
  gates, above);
- the `.md` has an `## Exit codes` section but omits a code the CLI can return
  (an exit code that appears as an integer literal returned by `main` or passed to
  `sys.exit`).

**Limitations (by design).** Non-numeric defaults (paths, `None`, prose like
"current interpreter") are never gated -- docs legitimately paraphrase dynamic
defaults. A fenced usage synopsis that does not start with `usage:` (a hand-written
`python tool.py --path PATH ...` line) is not parsed for coverage; only a pasted
`--help` block is. Exit-code checking is skipped
entirely when the `.md` has no `## Exit codes` section (rather than guessing from
scattered prose). The section runs to the next Markdown heading; a `#` line inside a
fenced code block (a shell comment) is not a heading and does not end it. These conservative choices keep Check B from ever false-failing
a build on parse ambiguity.

Discovery recognizes a parser by its class name: `ArgumentParser` itself, or a subclass
whose name ends with `ArgumentParser`. A tool whose parser comes from a subclass named
otherwise (`class TerminalSafeParser(argparse.ArgumentParser)`, say) is not discovered, so
a directory walk silently skips it, and a single-file `--path` naming it exits `2` with
`--path is not an argparse CLI`.

### Check C -- stated floor vs version-gated imports

A module that imports a version-gated stdlib module (`tomllib` needs 3.11,
`zoneinfo` / `graphlib` need 3.9) must not claim a *lower* `3.x+` floor. Check C
reads the stated floor from the three canonical locations only -- the **module
docstring**, the **help-formatter class docstring**, and the argparse **epilog**
(plus the `.md`) -- so an example floor mentioned in a comment or some other
docstring is not mistaken for a claim. Only an *unconditional module-level* import
raises the floor, and a gate whose backport is also imported (`tomli` for
`tomllib`) does not, so a `try: import tomllib / except: import tomli` shim is not
flagged.

### Check D -- no positional arguments (opt-in)

Check D runs only when `--check-positionals` or `check_positionals = true` turns it on,
because it enforces a policy about how a CLI should look rather than a drift between a
doc and its code. The policy: user-facing CLI arguments should be **named options**,
never positional, because a named invocation is self-documenting and order-independent.
Check D walks every `add_argument(...)` and flags a call whose
**first argument** is a string literal that does not start with `-` (a positional
name like `add_argument("value")`). An option string (`--value`, `-v`) starts with
`-` and is fine; a call with no positional argument (`add_argument(dest=...)`) or
whose first argument is a non-literal (a variable or expression) cannot be
statically proven positional and is skipped, so the check never guesses. This is a
gating finding: fix it by turning the positional into a named option
(`add_argument("--value", ...)`).

## Usage

```text
python cli_doc_consistency.py --path PATH [--exclude PATH ...] [--json] [--allow-missing-doc]
                              [--doc PATH] [--run-help | --no-run-help] [--check-positionals]
```

| Argument | Required | Default | Description |
| --- | --- | --- | --- |
| `--path PATH` | yes | — | A Python CLI file, or a directory walked for CLI files. |
| `--exclude PATH` | no | `[]` | File/dir to skip while walking (repeatable); **unioned** with `[tool.cli_doc_consistency].exclude`. |
| `--json` | no | `False` | Emit a machine-readable JSON report. |
| `--allow-missing-doc` | no | `False` | Downgrade a missing companion `.md` from gating to advisory. |
| `--doc PATH` | no | `None` | The doc to check instead of the sibling `<name>.md` (a README, say). Needs `--path` to name one CLI file: with a directory it exits `2`, and so does a `--doc` that does not exist. |
| `--run-help` | no | `False` | **Executes** `<python> <tool> --help` to compare documented defaults (see the security note above). Also on with `run_help = true`, which the scanned tree's own config can set. |
| `--no-run-help` | no | `False` | Never execute the target: beats `--run-help` and `run_help = true`, so it is how to scan a tree you do not trust. Cannot be combined with `--run-help`. |
| `--check-positionals` | no | `False` | Run Check D (no positional CLI arguments). Also on with `check_positionals = true`. |

Every optional's default is auto-derived from its live `default=` in `--help` (via
`argparse.ArgumentDefaultsHelpFormatter`).

### Config: `[tool.cli_doc_consistency]`

cli_doc_consistency reads a `[tool.cli_doc_consistency]`
table from the **nearest** `pyproject.toml` at or above `--path` (only that one: a
nested project's pyproject replaces the root's table, it does not add to it). Its keys:

| Key | Type | Meaning |
| --- | --- | --- |
| `exclude` | list of paths | Standing exclusions, relative to that file's directory, **unioned** with any `--exclude`. A directory walk skips them; an explicitly named file is always analyzed. |
| `run_help` | boolean | `true` turns `--run-help` on: each CLI's `--help` is **executed**. Because this file sits in the scanned tree, `--no-run-help` overrides it. |
| `check_positionals` | boolean | `true` turns `--check-positionals` on. |

A flag turns its switch on even where the key is absent or `false`. `--no-run-help` is
the one way to turn a key off from the command line, and it exists because the tree
under scan supplies the config. A `pyproject.toml` the walk finds but **cannot
parse**, an `exclude` that is not a list of strings, or a switch that is not a TOML
boolean (a quoted `"true"` or a `1`) stops the run with `ERROR:` on stderr and exit `2`,
rather than running with the setting silently dropped.

Because of the nearest-file rule above, a repository that wants a switch on everywhere
sets it in the root `pyproject.toml` and repeats it in every nested project's.

## Exit codes

- `0` -- clean: no gating finding (advisory findings may still be reported).
- `1` -- a gating finding: Check A, a Check B coverage / no-option-surface / missing-doc /
  numeric-default contradiction, Check C, or (when turned on) Check D.
- `2` -- could not run: a missing, non-`.py`, undecodable or unparseable `--path`, a
  `--path` file that is not an argparse CLI, a `--doc` with a directory `--path` or
  naming no file, a doc that is not UTF-8, or a malformed config.

## Worked example

Run against the shipped sample (from the tool's folder). Real output, captured
verbatim:

```text
python cli_doc_consistency.py --path samples/widget/widget.py
```

```text
===== samples/widget/widget.py =====
  samples/widget/widget.py:25  [A gating] --count  - help text hardcodes '(default...)' but has no %(default) token; ArgumentDefaultsHelpFormatter appends its own '(default: X)', so the default double-prints and can drift
      fix: drop the literal '(default: ...)' and let the formatter render it, or reference it with the %(default)s placeholder
  samples/widget/widget.md  [B gating] --mode  - CLI option --mode is not documented in the doc's arguments table or --help block
      fix: document --mode in the doc (a table row, or the pasted --help block)
Checked 1 CLI file(s): 2 gating finding(s), 0 advisory.
CLI_DOC_CONSISTENCY FINAL RESULT: FAIL
```

Exit code: `1`. The sample `widget` intentionally hardcodes a default in its
`--count` help (Check A) and omits `--mode` from its `.md` table (Check B), so a
by-hand run shows both catches. `samples/readme_tool/` is the README case: a doc named
with `--doc` that carries a pasted `--help` block. See `samples/README.md` for both.

## Testing cli_doc_consistency itself

```text
python cli_doc_consistency/test_cli_doc_consistency.py
```

## Which files are read, and how

The walk skips `test_*.py`, virtualenvs (by their `pyvenv.cfg` marker), caches
(`.git`, `__pycache__`, `.mypy_cache`, `.ruff_cache`, `node_modules`, `.venv`) and
packaging output (`build`, `dist`, `.tox`, `.nox`, where `pip install .` and test
runners leave copies of each CLI without its doc). Source is decoded the way Python
decodes it: its coding cookie or BOM, else UTF-8. In a walk, a file that cannot be read,
decoded or parsed is skipped, so one bad file never wedges the gate; a `--path` naming
such a file is exit `2`. That includes source built to hurt the parser: a coding cookie
naming a codec that is not a text encoding, and nesting deep enough to overflow the
parser's or compiler's stack. A doc must be UTF-8: one that is not is exit `2`.

**Hostile input.** The doc readers do work linear in the doc's size: a help entry's
default is found from its last `(default:` marker, not by a scan from each one, and a
help entry is cut at 4,096 characters however many continuation lines it has. In the
text report every string the scanned tree controls (file names, option literals, the
messages that quote them) has terminal control characters spelled as escapes (`\x1b`,
`\x0a`), so a name cannot repaint the terminal or print a line of its own, such as a
forged verdict. The `--json` report escapes them by being JSON. The child's output under
`--run-help` is bounded in time (30 seconds) but not in size.

## When nothing is examined: `NOTHING EXAMINED`

A bare `0 CLI file(s) checked` + `PASS` cannot be told apart from a verified-clean run, so an
`exclude` entry broader than its author meant would report a clean gate — for a tree nobody
is checking — indefinitely. When the effective exclude set prunes **every** candidate under
`--path`, the report therefore says outright that it verified nothing, and names how many
CLI files were hidden:

```text
NOTHING EXAMINED: all 3 candidate CLI file(s) under --path were pruned by the exclude set
([tool.cli_doc_consistency] exclude and/or --exclude), so this result verifies nothing. …
```

A run that found no CLI at all, with nothing excluded, says so too (`NOTHING EXAMINED: no
argparse CLI file ... was found under --path`). The `--json` report carries the same line
as `caveat` (null when a CLI was examined) beside `examined`, the count of CLI files
checked.

The verdict deliberately stays `PASS` — every standing exclusion is intentional, and a
folder with no CLI is an ordinary subject of a whole-tree sweep, so failing on either
would break it. The wording and the when-to-speak rule are not this
tool's own: they come from the shared `nothing_examined` library, so every
discovery-based gate says this identically. See `nothing_examined.md` for the rationale.
