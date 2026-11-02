# Changelog

Notable changes to this project, newest first.

## 0.3.0 - 2026-11-02

### Changed

- `cli_doc_consistency --help` no longer prints `(default: False)`, `(default: None)`,
  or `(default: [])`: an option left out shows no default.
- For a library caller of `cli_doc_consistency.build_parser()`, each flag left out
  (`--json`, `--allow-missing-doc`, `--run-help`, `--no-run-help`, and
  `--check-positionals`) now reads `None`, not `False`. Test a flag for truth; a check
  for `is False` no longer matches.
- `shellcheck_md --help` prints `--files` last in its usage line, as the `shellcheck_md.md`
  synopsis does, because every argument after `--files` is a file name.
- Some printed text is reworded, with no change in meaning: the `shellcheck_md` and
  `cli_doc_consistency` `--help` text, the message `shellcheck_md` prints when Windows
  refuses an over-long command line, and the message a test suite prints when it finds
  fewer tests than it expects.

### Fixed

- The `shellcheck_md.md` usage synopsis now shows `--files` last. The old synopsis put
  options after `--files`, where the tool reads every argument as a file name, so a
  command copied from it exited 2.
- A `shellcheck_md` test expected a refused file name with `/` separators, so it failed
  on Windows. It now expects the name as the tool prints it on the host.

### Documentation

- The README has an install section that creates a virtual environment, because
  Homebrew and Debian Python refuse a system-wide `pip install`. Its commands, and the
  test commands, run from the clone with the venv active.
- Comments, docstrings, and the tools' and libraries' documents were reworded for readers
  new to the project.
- `pyproject.toml` no longer carries configuration for checks that do not ship with this
  repository.

## 0.2.0 - 2026-10-27

- Adds `cli_doc_consistency`, which holds a command-line tool's doc to its `argparse`
  definition, and the `pyproject_config` library it uses to read its
  `[tool.cli_doc_consistency]` table from the nearest `pyproject.toml`.

## 0.1.0 - 2026-10-27

- Initial release, with `shellcheck_md`.
