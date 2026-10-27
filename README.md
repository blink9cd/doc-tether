# doc-tether

Keep your docs tethered to the code and commands they describe. When a doc drifts, the check
fails your build instead of letting it slide.

Each tool lives in its own folder with its own guide; the folders beside it that hold no tool
are the shared libraries the tools and their tests use, shipped with their own tests.

| Tool | What it checks |
| --- | --- |
| [`shellcheck_md`](shellcheck_md/shellcheck_md.md) | The `bash` and `sh` code blocks in Markdown, run through ShellCheck |

More checks of the same kind are planned, such as one holding a command-line tool's guide to
the options its `--help` actually offers.

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

From a clone, lint every Markdown file under a folder, or just the files you name:

```bash
python3.12 doc-tether/shellcheck_md/shellcheck_md.py --root docs
python3.12 doc-tether/shellcheck_md/shellcheck_md.py --files README.md CONTRIBUTING.md
```

Or install it, which puts a `shellcheck-md` command and ShellCheck itself on the
environment's `PATH`:

```bash
python3.12 -m pip install ./doc-tether
shellcheck-md --root docs
```

Exit codes: `0` clean, `1` ShellCheck reported findings, `2` the run could not be trusted: a
`pyproject.toml` it could not read, a `--files` entry that is not a file, a Markdown file
that is not UTF-8 text, a temporary script it could not write, a `.shellcheckrc` that is a symbolic link, or a ShellCheck that could
not be started, outlasted `--timeout-seconds`, or exited with a status that carries no
verdict. When ShellCheck reports that it could not process some blocks, or a later run of it
reaches no verdict, the findings already made are printed before the exit `2`. Every option
is in [`shellcheck_md/shellcheck_md.md`](shellcheck_md/shellcheck_md.md).

### How a finding points back at your doc

Each block becomes a temporary script, with a shebang added so ShellCheck picks the right
dialect. Each finding ShellCheck reports against that script is mapped back to the Markdown
file, line and column it came from:

```text
samples/embedded_shell.md:26:6: note: Double quote to prevent globbing and word splitting. [SC2086]
Checked 2 Markdown file(s): 1 shellcheck finding(s).
```

That output is the shipped sample's, from
`python3.12 shellcheck_md.py --root samples --lang ksh` run inside `shellcheck_md/`;
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

## Running the tests

The suites need Hypothesis, at the version the `test` extra in `pyproject.toml` pins.
Installing the package with that extra brings it:

```bash
python3.12 -m pip install './doc-tether[test]'
```

Each suite then runs from its own folder's test file. Only shellcheck_md's live boundary
cases run the real ShellCheck, against temporary files and an rc file of their own, and they
skip when none is on your `PATH`; one more starts a stand-in script in its place:

```bash
python3.12 doc-tether/shellcheck_md/test_shellcheck_md.py
python3.12 doc-tether/argv_budget/test_argv_budget.py
python3.12 doc-tether/bounded_run/test_bounded_run.py
python3.12 doc-tether/help_formatter/test_help_formatter.py
python3.12 doc-tether/live_pid_guard/test_live_pid_guard.py
python3.12 doc-tether/nothing_examined/test_nothing_examined.py
python3.12 doc-tether/stdio_utf8/test_stdio_utf8.py
python3.12 doc-tether/venv_prune/test_venv_prune.py
```

## License

AGPL-3.0-or-later.
