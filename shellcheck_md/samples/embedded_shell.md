# Sample input for shellcheck_md

This Markdown file embeds two fenced shell blocks so the tool can be run by hand
and its findings eyeballed. See `README.md` in this directory for the exact
commands and the expected output.

## A clean block (no findings)

The variable is quoted, so shellcheck reports nothing here:

```bash
name="documentation example"
echo "Hello, ${name}"
```

## A block with a deliberate SC2086 finding

The `$target` expansion below is unquoted, so shellcheck flags SC2086, and the
tool remaps that finding from the temporary script it lints back to this file
and line. The block is tagged `ksh` (not `bash`/`sh`) on purpose: a default
run of the tool skips it, so this deliberately-dirty sample never fails
one, yet a hand run with `--lang ksh` surfaces the finding.

```ksh
target=$1
echo $target
```
