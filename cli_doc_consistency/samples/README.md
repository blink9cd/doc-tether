# cli_doc_consistency samples

Two tiny fake CLIs whose docs are intentionally drifted from their code, so one
command each demonstrates the tool catching real drift by hand. Run both from the tool's
folder.

## `widget/`: a companion `.md` with an arguments table

```text
python cli_doc_consistency.py --path samples/widget/widget.py
```

Expected (real output; exit `1`):

```text
===== samples/widget/widget.py =====
  samples/widget/widget.py:25  [A gating] --count  - help text hardcodes '(default...)' but has no %(default) token; ArgumentDefaultsHelpFormatter appends its own '(default: X)', so the default double-prints and can drift
      fix: drop the literal '(default: ...)' and let the formatter render it, or reference it with the %(default)s placeholder
  samples/widget/widget.md  [B gating] --mode  - CLI option --mode is not documented in the doc's arguments table or --help block
      fix: document --mode in the doc (a table row, or the pasted --help block)
Checked 1 CLI file(s): 2 gating finding(s), 0 advisory.
CLI_DOC_CONSISTENCY FINAL RESULT: FAIL
```

`--count` hardcodes `(default: 5)` in its help (Check A); `--mode` exists in the
code but is missing from `widget.md`'s table (Check B).

## `readme_tool/`: a README carrying a pasted `--help` block

Its doc is `README.md`, not a sibling `readme_tool.md`, so name it with `--doc` (which
needs `--path` to be the one CLI file):

```text
python cli_doc_consistency.py --path samples/readme_tool/readme_tool.py --doc samples/readme_tool/README.md --run-help
```

Expected (real output; exit `1`):

```text
===== samples/readme_tool/readme_tool.py =====
  samples/readme_tool/README.md  [B gating] --format  - CLI option --format is not documented in the doc's arguments table or --help block
      fix: document --format in the doc (a table row, or the pasted --help block)
  samples/readme_tool/README.md  [B gating] --limit  - documented default 10 for --limit does not match the actual default 20
      fix: update the doc's default for --limit to 20
Checked 1 CLI file(s): 2 gating finding(s), 0 advisory.
CLI_DOC_CONSISTENCY FINAL RESULT: FAIL
```

`--format` is missing from the pasted block: that finding comes from the AST and needs
nothing executed. The `--limit` default is caught only because `--run-help` runs
`readme_tool.py --help` to read the real default. Where the nearest `pyproject.toml`
sets `run_help = true`, the run executes it with or without the flag. Swap
`--run-help` for `--no-run-help` and nothing is executed: the `--limit`
finding becomes one advisory saying the defaults were not checked.
