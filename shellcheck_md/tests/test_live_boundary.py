"""Grouped suite: the process boundary, crossed for real rather than faked.

Every other suite replaces the run seam, which is what keeps them host-independent, and
also why none of them could see the seam drop ShellCheck's exit status. These cases start
a real process. One stands a tiny executable in for ShellCheck and always runs; the rest
run the real ShellCheck and skip, saying why, on a host that has none.

Host isolation: each case works in its own temporary directory, removes
``SHELLCHECK_OPTS`` unless the case sets it, and names a ``.shellcheckrc`` inside that
directory, so neither the host's environment nor its own rc file can change the verdict.
"""

from __future__ import annotations

import io
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import shellcheck_md as sut  # noqa: E402 - import follows the sys.path bootstrap above

# Line 4 of the Markdown is `echo $x`, and SC2086 (unquoted expansion) at its column 6 is
# the only finding: x is assigned first, so SC2154 (referenced but not assigned) is not,
# and from a command substitution, whose value ShellCheck cannot prove free of spaces.
_DIRTY = '# Doc\n```bash\nx="$(pwd)"\necho $x\n```\n'
_CLEAN = '# Doc\n```bash\necho "$HOME"\n```\n'
_REAL_SHELLCHECK = shutil.which("shellcheck")
_SKIP_REASON = "no shellcheck executable on PATH; the live boundary cannot be crossed"


def _fake_backend(directory: Path, exit_status: int) -> Path:
    """Write an executable that ignores its arguments and exits *exit_status*."""
    if os.name == "nt":  # pragma: only-on win32
        return _batch_backend(directory, exit_status)
    return _sh_backend(directory, exit_status)  # pragma: only-on posix


def _batch_backend(directory: Path, exit_status: int) -> Path:  # pragma: only-on win32
    """Windows: a batch file, which CreateProcess starts through cmd.exe."""
    script = directory / "fake_shellcheck.bat"
    script.write_text(f"@exit /b {exit_status}\r\n", encoding="ascii", newline="")
    return script


def _sh_backend(directory: Path, exit_status: int) -> Path:  # pragma: only-on posix
    """POSIX: a /bin/sh script marked executable."""
    script = directory / "fake_shellcheck"
    script.write_text(
        f"#!/bin/sh\nexit {exit_status}\n", encoding="ascii", newline="\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    return script


def _run_main(
    argv: list[str], env: dict[str, str] | None = None
) -> tuple[int, str, str]:
    """Run ``main`` with the real seam; *env* adds variables, SHELLCHECK_OPTS removed."""
    out, err = io.StringIO(), io.StringIO()
    with (
        mock.patch.dict(os.environ, env or {}),
        mock.patch("sys.stdout", out),
        mock.patch("sys.stderr", err),
    ):
        if env is None or "SHELLCHECK_OPTS" not in env:
            os.environ.pop("SHELLCHECK_OPTS", None)
        code = sut.main(argv)
    return code, out.getvalue(), err.getvalue()


class _LiveCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        # An rc file of our own, so the host's ~/.shellcheckrc never applies here.
        (self.root / ".shellcheckrc").write_text(
            "# empty: isolates this case from the host's rc file\n",
            encoding="utf-8",
            newline="\n",
        )

    def _write(self, body: str) -> Path:
        md = self.root / "doc.md"
        md.write_text(body, encoding="utf-8", newline="\n")
        return md


class FakeBackendTests(_LiveCase):
    """Always runs: a real process, but one whose behavior this suite controls."""

    def test_a_backend_exiting_3_is_could_not_run(self) -> None:
        backend = _fake_backend(self.root, 3)
        md = self._write(_DIRTY)
        code, out, err = _run_main(["--shellcheck", str(backend), "--files", str(md)])
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("exited 3", err)

    @unittest.skipIf(os.name == "nt", "a marker-writing /bin/sh payload needs POSIX")
    def test_a_staged_name_spelled_as_an_option_never_runs_as_the_backend(self) -> None:
        """A file named ``--shellcheck=<payload>`` after ``--files`` is a file name.

        pre-commit appends staged names after ``--files``; parsed as options, this one
        made the payload the backend, which ran and reported a clean pass.
        """
        marker = self.root / "payload-ran"
        payload = self.root / "payload.md"
        payload.write_text(
            f"#!/bin/sh\ntouch '{marker}'\nexit 0\n", encoding="ascii", newline="\n"
        )
        payload.chmod(payload.stat().st_mode | stat.S_IXUSR)
        md = self._write(_CLEAN)
        code, out, err = _run_main(["--files", str(md), f"--shellcheck={payload}"])
        self.assertFalse(marker.exists(), "the payload was run as shellcheck")
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn(f"not a file: --shellcheck={payload}", err)


@unittest.skipIf(_REAL_SHELLCHECK is None, _SKIP_REASON)
class RealShellcheckTests(_LiveCase):
    def _shellcheck(self) -> str:
        assert _REAL_SHELLCHECK is not None  # narrowed for mypy; skipIf guarantees it
        return _REAL_SHELLCHECK

    def test_a_dirty_fence_is_exit_1_at_its_markdown_location(self) -> None:
        md = self._write(_DIRTY)
        code, out, _err = _run_main(
            ["--shellcheck", self._shellcheck(), "--files", str(md)]
        )
        self.assertEqual(code, 1)
        self.assertIn(f"{md}:4:6: note:", out)
        self.assertIn("[SC2086]", out)

    def test_a_clean_fence_is_exit_0(self) -> None:
        md = self._write(_CLEAN)
        code, out, _err = _run_main(
            ["--shellcheck", self._shellcheck(), "--files", str(md)]
        )
        self.assertEqual(code, 0)
        self.assertIn("0 shellcheck finding(s)", out)

    def test_shellcheck_opts_changing_the_format_is_not_a_clean_pass(self) -> None:
        md = self._write(_DIRTY)
        code, out, err = _run_main(
            ["--shellcheck", self._shellcheck(), "--files", str(md)],
            env={"SHELLCHECK_OPTS": "-f json"},
        )
        self.assertEqual(code, 2)
        self.assertEqual(out, "")
        self.assertIn("SHELLCHECK_OPTS", err)

    def test_the_projects_rc_file_disables_a_finding(self) -> None:
        (self.root / ".shellcheckrc").write_text(
            "disable=SC2086\n", encoding="utf-8", newline="\n"
        )
        md = self._write(_DIRTY)
        code, _out, _err = _run_main(
            ["--shellcheck", self._shellcheck(), "--files", str(md)]
        )
        self.assertEqual(code, 0)

    def test_an_undotted_rc_file_disables_a_finding(self) -> None:
        """The undotted name is found too, and still reaches the scripts as a copy."""
        sub = self.root / "sub"
        sub.mkdir()
        (sub / "shellcheckrc").write_text(
            "disable=SC2086\n", encoding="utf-8", newline="\n"
        )
        md = sub / "doc.md"
        md.write_text(_DIRTY, encoding="utf-8", newline="\n")
        code, _out, _err = _run_main(
            ["--shellcheck", self._shellcheck(), "--files", str(md)]
        )
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
