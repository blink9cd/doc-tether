# shellcheck_md samples

Hand-verification input for `shellcheck_md`. The single sample, `embedded_shell.md`,
carries two fenced shell blocks: one clean `bash` block and one deliberately-dirty
`ksh` block whose unquoted `$target` expansion triggers `SC2086`. All content is
placeholder text; nothing here embeds real PII.

## Why the dirty block is tagged `ksh`

`shellcheck_md` runs over this repository's own Markdown. If the deliberate
finding lived in a `bash`/`sh` block it would fail that run. Tagging it `ksh` -- a
real dialect shellcheck understands, but one outside the default `bash`/`sh` set --
keeps the sample green under a default run while still letting a hand run
surface the finding via `--lang ksh`. This doubles as a live demonstration of the
`--lang` option.

## Run it

From `shellcheck_md/`, the default run lints only `bash`/`sh`. It scans
both files here -- this `README.md` (only `text`-tagged fences, so no findings)
and `embedded_shell.md` -- so it sees just the clean block and exits 0:

```text
$ python shellcheck_md.py --root samples
Checked 2 Markdown file(s): 0 shellcheck finding(s).
```

Add `--lang ksh` to lint the dirty block. The finding is remapped from the
temporary script back to the sample's own `file:line:col`, and the tool exits 1:

```text
$ python shellcheck_md.py --root samples --lang ksh
samples/embedded_shell.md:26:6: note: Double quote to prevent globbing and word splitting. [SC2086]
Checked 2 Markdown file(s): 1 shellcheck finding(s).
```

Line 26 of `embedded_shell.md` is the unquoted `echo $target`; column 6 is the
`$`. That `<file>:<line>:<col>` remap -- mapping a finding on a synthesized
script back to the source Markdown -- is the tool's distinctive behavior.
