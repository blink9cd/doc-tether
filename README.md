# doc-tether

Keep your docs tethered to the code and commands they describe. When a doc drifts, the check
fails your build instead of letting it slide.

Each tool lives in its own folder with its own guide; the folders beside it that hold no tool
are the shared libraries the tools and their tests use, shipped with their own tests.

| Tool | What it checks |
| --- | --- |
| [`shellcheck_md`](shellcheck_md/shellcheck_md.md) | The `bash` and `sh` code blocks in Markdown, run through ShellCheck |
| [`cli_doc_consistency`](cli_doc_consistency/cli_doc_consistency.md) | A Python command-line tool's doc, held to the options its `argparse` parser defines |

Both install as pre-commit hooks and run directly from a clone; each section below says how.

## Install

Needs Python 3.12 or newer. Install into a virtual
environment, because Homebrew and Debian Python refuse a
system-wide `pip install`:

```bash
python3 -m venv .venv
# shellcheck source=/dev/null # the activate script exists only once the line above has run
source .venv/bin/activate
pip install .
```

The commands below assume the venv is active, so `python`
is the venv's interpreter.

Run the install from the root of your clone. It puts the `shellcheck-md` and
`cli-doc-consistency` commands on the venv's `PATH`, and ShellCheck with them. A pre-commit
hook needs none of this: pre-commit builds an environment of its own.

## shellcheck_md

A README's shell examples are code that no linter reads. A quoting mistake, an unset
variable or a heredoc that never ends ships in the docs and fails the first time someone
pastes it. shellcheck_md lifts every fenced `bash` and `sh` block out of your Markdown, runs
[ShellCheck](https://www.shellcheck.net/) over each one, and reports every finding at the
line and column of the Markdown file it came from. It exits non-zero when ShellCheck finds
anything, so it can fail a commit or a build.

- Code blocks indented under a list item are linted too, and a heredoc in one still works:
  the indentation is removed before ShellCheck sees the block and added back to each column
  it reports.
- Backtick and tilde fences are both read. A fence's language is the first word of its info
  string, in any case, so ```` ```Bash title="install" ```` is linted as `bash`.
- `--lang` picks other fence languages (`ksh`, `zsh` and so on); any value replaces the
  default `bash` and `sh` pair.
- Your project's `.shellcheckrc` applies: the one nearest each Markdown file is copied
  beside the blocks lifted from it, where ShellCheck finds it, so its `disable=` and other
  directives hold for the blocks in your docs as they do for your scripts, on any
  ShellCheck version. `SHELLCHECK_OPTS` applies too.
- A `[tool.shellcheck_md]` table in your `pyproject.toml` can hold standing excludes, and
  `--exclude` adds more for one run. Each Markdown file answers to the nearest
  `pyproject.toml` that has the table, whether it was found under `--root` or named with
  `--files`; one without the table is passed over, as ruff passes over one without
  `[tool.ruff]`.
- Findings print sorted by file, line and column, and every line the tool prints is escaped
  first, so a control character or direction override in a file name, a message or
  ShellCheck's output shows as `\x1b` or `\u202e` rather than acting on your terminal.
- Long file lists are split across as many ShellCheck runs as the operating system's
  command-line limit needs, and each run is time-bounded, so a hung ShellCheck is reported
  rather than waited on forever.
- ShellCheck's exit status is checked against what it printed. A run that reached no
  verdict, such as `SHELLCHECK_OPTS=-f json` changing the output format, fails with exit
  `2` rather than passing as clean.

### Requirements

- Python 3.12 or newer.
- ShellCheck. Installing this repository as a package, which pre-commit does for you,
  brings its own copy through the `shellcheck-py` package, and the tool finds it there even
  when that environment is not on your `PATH`, as with pipx. Otherwise the tool uses the
  `shellcheck` on your `PATH`, or the one `--shellcheck` names.

### Install as a pre-commit hook

Add this to your repository's `.pre-commit-config.yaml`, with this repository's URL and a
release tag in place of the placeholders:

```yaml
repos:
  - repo: <repository-url>
    rev: <tag>
    hooks:
      - id: shellcheck-md
```

pre-commit then runs the hook on the Markdown files staged in each commit. The hook's
default arguments are `--files`, which takes every file pre-commit appends after it.
`--files` must come last: every argument after it is a file name, never an option, so a
staged file named like an option, such as `--shellcheck=x.md`, is linted as a file and
cannot change what the tool runs. To pass options of your own, set `args`, and always end
your own `args` with `--files`; without it, a staged file named like an option would be read
as one:

```yaml
      - id: shellcheck-md
        args: [--lang, bash, --lang, ksh, --files]
```

### Run it directly

Once installed, lint every Markdown file under a folder, or just the files you name:

```bash
shellcheck-md --root docs
shellcheck-md --files README.md CONTRIBUTING.md
```

The script also runs straight from the clone, with `doc-tether` standing for the clone's
path. It then uses the `shellcheck` on your `PATH`, since nothing installed one beside it:

```bash
python doc-tether/shellcheck_md/shellcheck_md.py --root docs
```

Exit codes: `0` clean, `1` ShellCheck reported findings, `2` the run could not be trusted: a
`pyproject.toml` it could not read, a `--files` entry that is not a file, a Markdown file
that is not UTF-8 text, a temporary script it could not write, a `.shellcheckrc` that is a
symbolic link, or a ShellCheck that could not be started, outlasted `--timeout-seconds`, or
exited with a status that carries no verdict. When ShellCheck reports that it could not
process some blocks, or a later run of it reaches no verdict, the findings already made are
printed before the exit `2`. Every option is in
[`shellcheck_md/shellcheck_md.md`](shellcheck_md/shellcheck_md.md).

### How a finding points back at your doc

Each block becomes a temporary script, with a shebang added so ShellCheck picks the right
dialect. Each finding ShellCheck reports against that script is mapped back to the Markdown
file, line and column it came from:

```text
samples/embedded_shell.md:26:6: note: Double quote to prevent globbing and word splitting. [SC2086]
Checked 2 Markdown file(s): 1 shellcheck finding(s).
```

That output is the shipped sample's, from
`python shellcheck_md.py --root samples --lang ksh` run inside `shellcheck_md/`;
[`shellcheck_md/samples/README.md`](shellcheck_md/samples/README.md) walks through it.

### Known limits

- A fence inside a blockquote is not linted, since each of its lines starts with `>`.
- Inline code spans are not linted: a command between single backticks in a sentence is
  usually a fragment, not something anyone runs.
- A block tagged with a language outside the ones being linted, such as `shell`, is
  skipped. Tag a transcript with prompts and output as `console` to keep it out on purpose.
- A shell fence shown inside another fence, as in a Markdown example of a Markdown file, is
  linted too: the scanner follows only the fences it lints.
- Each block is checked as a script in a temporary folder, so a `source` of a relative path
  finds nothing and ShellCheck reports SC1091. Disable SC1091 in your `.shellcheckrc`, or
  keep sourced helpers out of linted examples. A finding ShellCheck reports on a file other
  than the block itself is not shown.
- Your `.shellcheckrc` is read from that temporary folder too, so a relative `source-path`
  in it resolves there rather than in your project.
- A repository's `.shellcheckrc` is honored as ShellCheck would honor it. One that sets
  `external-sources=true` or a `source-path` lets ShellCheck read the files a block sources,
  wherever they are; it never runs them. Linting a repository you do not trust means
  trusting its rc file that far.
- A `.shellcheckrc` or `shellcheckrc` that is a symbolic link is refused, with exit `2`
  naming it, rather than followed: copying it would read whatever file it points to. Make
  it a regular file.
- A symbolic link to a Markdown file is followed, and the file it points to is linted.
- A `[tool.shellcheck_md]` exclude hides files from the run by design, including a
  repository's own docs; an exclude that hides every file says so in the output.
- A file named with `--files` is linted as named: the directory and virtualenv pruning that
  `--root` applies does not apply to it, though both kinds of exclude do.

## cli_doc_consistency

A command-line tool's README lists its options once, by hand, and the parser goes on
changing. An option is added and never documented, or renamed and still documented under
its old name. cli_doc_consistency reads the `argparse` parser out of each tool's source and
fails when the doc and the parser disagree. It exits non-zero on a mismatch, so it can fail
a commit or a build.

It finds every Python file that constructs an `ArgumentParser` (or a subclass whose name
ends with it) and pairs each with its doc: the `<name>.md` beside it, or, for one file, the
doc `--doc` names. Then:

- **Every long option is documented, and no other is.** A `--long` option the parser
  defines but the doc omits fails, and so does one the doc lists that the parser does not
  define. A doc with no option listing at all fails too, since there would be nothing to
  compare. Positional arguments and short-only options (`-v` with no long form) are not
  compared.
- **A `help=` text does not hand-write its default.** `(default: 5)` typed into a help
  string prints twice under `ArgumentDefaultsHelpFormatter` and goes stale when the default
  changes.
- **A stated Python floor covers the imports.** A docstring, epilog or doc claiming `3.9+`
  while the module imports `tomllib` (3.11) fails.
- **Named options only, if you ask.** `--check-positionals` fails any positional argument.
  It is a style rule rather than drift, so it is off unless you turn it on.

The doc can list the options in either of two shapes, or both:

- **An arguments table:** a Markdown table whose first header cell reads `Argument`,
  `Option` or `Flag`, with each option's `--name` in the first cell. A `Default` column is
  read too.
- **A pasted `--help` block:** a fenced code block holding the tool's `--help` output,
  starting at its `usage:` line, or at a `$ tool --help` prompt line just above it. Options
  are read from argparse's `options:` sections and from the argument groups your parser
  declares.

### Requirements

- Python 3.12 or newer: the package requires it, and the whole repository is tested at
  3.12. The tool's own guide says 3.11+, the least its source needs; 3.12 is the supported
  floor. Nothing else: it reads your code with the standard library's `ast`.

### Install as a pre-commit hook

The hook runs whenever a commit stages a Python or Markdown file, since either side can
drift. pre-commit passes it no file names: it runs once, with the `args` you give it, so a
staged file named like an option can never be read as one.

When your CLI is documented in your README, which is the common case, give the hook one
entry per CLI, naming the tool and its doc:

```yaml
repos:
  - repo: <repository-url>
    rev: <tag>
    hooks:
      - id: cli-doc-consistency
        args: [--path, src/pkg/cli.py, --doc, README.md, --no-run-help]
```

Add one more `- id: cli-doc-consistency` entry, with its own `args`, for each further CLI.
Drop `--no-run-help` from an entry only to compare the doc's defaults with the live
`--help`, which runs your code (see *Running your code* below).

The hook's default arguments are `--path . --no-run-help`, which checks every CLI in the
repository against the `<name>.md` beside it: `<dir>/<name>.py` against `<dir>/<name>.md`.
That form works only when each CLI has such a file; for a CLI documented only in a README it
fails with a missing companion doc. Use it, with your own options added, where that holds:

```yaml
      - id: cli-doc-consistency
        args: [--path, ., --no-run-help, --check-positionals]
```

The default arguments never execute the code being checked, even when the checked tree's
`pyproject.toml`, or one above it, sets `run_help = true`: `--no-run-help` overrides that
key. pre-commit replaces the default arguments with any `args` you set, so an `args` list
without `--no-run-help` lets the key, or a `--run-help` you add, run each CLI's `--help`.
Do that only for code you trust, and not on pull requests you do not.

### Run it directly

Once installed, check every CLI under a folder, or one tool against a README:

```bash
cli-doc-consistency --path src
cli-doc-consistency --path src/tool.py --doc README.md
```

The script also runs straight from the clone, with `doc-tether` standing for the clone's
path:

```bash
python doc-tether/cli_doc_consistency/cli_doc_consistency.py --path src
```

A `[tool.cli_doc_consistency]` table in the nearest `pyproject.toml` at or above `--path` can
hold standing `exclude` paths and turn on `run_help` and `check_positionals`. The nearest
`pyproject.toml` is the one read whether or not it has the table, so a nested project's file
without one leaves the check unconfigured there. shellcheck_md differs: it reads the nearest
`pyproject.toml` that has its table. Exit codes: `0`
clean, `1` a mismatch, `2` the run could not be trusted: a path that is not a Python CLI, a
`--doc` with a directory `--path`, or a `pyproject.toml` it could not read. Every option is
in [`cli_doc_consistency/cli_doc_consistency.md`](cli_doc_consistency/cli_doc_consistency.md),
and [`cli_doc_consistency/samples/README.md`](cli_doc_consistency/samples/README.md) walks
through a run on each doc shape.

### Running your code: off by default

By default the check only reads your source, through Python's `ast`, and executes nothing.
`--run-help` adds one step: it runs `python <tool> --help` for each CLI it finds, from the
tool's own folder, to read the real defaults and compare them with the ones the doc states.
That executes the tool's module-level code and whatever its `--help` path runs. Without it,
a doc that states defaults gets one note saying they were not checked.

`run_help = true` in `[tool.cli_doc_consistency]` turns the same step on, and that
`pyproject.toml` is normally inside the tree being checked, so a repository you did not
write can turn execution on by itself. To check a tree you do not trust, pass
`--no-run-help`: it overrides both `--run-help` and the config, and nothing under `--path` is
executed.

### Known limits

- Only `argparse` is read. A tool built on another parser library is not found.
- Positional arguments and short-only options are not compared with the doc: an
  undocumented `-v` or `input` passes. Only options with a `--long` name are.
- A parser is recognized by its class name: `ArgumentParser`, or a subclass whose name ends
  with it. A tool whose parser comes from a subclass named otherwise, such as
  `class SafeParser(argparse.ArgumentParser)`, is skipped by a folder walk, and naming its
  file in `--path` exits `2`, reporting it is not an argparse CLI.
- Options are found anywhere in the tool's own file, helpers included, but a call into
  another module is not followed: options added in a second file are invisible.
- A hand-written usage line that does not start with `usage:` is not read as a `--help`
  block; only pasted `--help` output is.
- An argument group whose title is computed rather than a string literal cannot be matched
  to its section of a pasted `--help` block. Give it a literal title, or list its options in
  a table.
- Only a purely numeric default is compared, and only with `--run-help`. Paths, `None` and
  defaults described in words are left alone, since docs paraphrase those.
- Exit codes are compared only when the doc has an `## Exit codes` section, and a missing
  code is a note, never a failure.

## Running the tests

The suites need Hypothesis, at the version the `test` extra in `pyproject.toml` pins.
Installing the package with that extra, from the clone's root with the venv active, brings
it:

```bash
pip install '.[test]'
```

Each suite then runs from its own folder's test file, here from the clone's root. Only
shellcheck_md's live boundary cases run the real ShellCheck, against temporary files and an
rc file of their own, and they skip when none is on your `PATH`; one more starts a stand-in
script in its place. cli_doc_consistency's live cases run small tools of their own, written
to a temporary folder, with the interpreter running the suite:

```bash
python shellcheck_md/test_shellcheck_md.py
python cli_doc_consistency/test_cli_doc_consistency.py
python argv_budget/test_argv_budget.py
python bounded_run/test_bounded_run.py
python help_formatter/test_help_formatter.py
python live_pid_guard/test_live_pid_guard.py
python nothing_examined/test_nothing_examined.py
python pyproject_config/test_pyproject_config.py
python stdio_utf8/test_stdio_utf8.py
python venv_prune/test_venv_prune.py
```

## License

AGPL-3.0-or-later.
