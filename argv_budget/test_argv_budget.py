#!/usr/bin/env python3
"""Unit tests for argv_budget -- bare-runnable (python test_argv_budget.py).

Every function is pure -- no filesystem, no subprocess -- so every branch is reachable
without a seam and 100% branch coverage needs no pragma.
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

# The library under test sits beside this file; import it by location, not by the working
# directory, so the test runs from any cwd.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import argv_budget  # noqa: E402 - import follows the sys.path bootstrap above


class RenderedLengthTest(unittest.TestCase):
    """Widths come from the renderer the subprocess layer actually uses."""

    def test_matches_list2cmdline(self) -> None:
        args = ["ruff", "format", "--check", "a.py"]
        self.assertEqual(
            argv_budget.rendered_length(args), len(subprocess.list2cmdline(args))
        )

    def test_a_space_bearing_token_costs_its_quotes(self) -> None:
        """A raw len() under-reports a quoted token, so a batch sized that way overflows."""
        spacey = "C:/dir with spaces/f.py"
        plain = "C:/dir/f.py"
        self.assertEqual(argv_budget.rendered_length([spacey]), len(spacey) + 2)
        self.assertGreater(
            argv_budget.rendered_length([spacey])
            - argv_budget.rendered_length([plain]),
            len(spacey) - len(plain),
        )

    def test_an_empty_argv_renders_nothing(self) -> None:
        self.assertEqual(argv_budget.rendered_length([]), 0)

    def test_joining_spaces_are_counted(self) -> None:
        self.assertEqual(argv_budget.rendered_length(["ab", "cd"]), 5)  # "ab cd"


class ArgvLimitForTest(unittest.TestCase):
    """Which ceiling binds is a property of what is LAUNCHED, not of the platform."""

    def test_cmd_and_bat_shims_get_the_cmd_ceiling(self) -> None:
        for name in ("C:/npm/markdownlint-cli2.CMD", "C:/npm/x.cmd", "C:/npm/x.BAT"):
            with self.subTest(name=name):
                self.assertEqual(
                    argv_budget.argv_limit_for(name), argv_budget.CMD_SHIM_ARGV_LIMIT
                )

    def test_real_executables_get_the_create_process_ceiling(self) -> None:
        for name in ("C:/py/python.exe", "/usr/bin/ruff", "markdownlint-cli2"):
            with self.subTest(name=name):
                self.assertEqual(
                    argv_budget.argv_limit_for(name),
                    argv_budget.CREATE_PROCESS_ARGV_LIMIT,
                )

    def test_the_shim_ceiling_is_the_tighter_one(self) -> None:
        self.assertLess(
            argv_budget.CMD_SHIM_ARGV_LIMIT, argv_budget.CREATE_PROCESS_ARGV_LIMIT
        )


class BatchPathsTest(unittest.TestCase):
    """The splitter fits every batch to the budget and drops nothing."""

    @staticmethod
    def _wide(count: int, width: int = 40) -> list[Path]:
        return [Path(f"{'q' * width}{i}.py") for i in range(count)]

    def test_a_list_that_fits_is_a_single_batch(self) -> None:
        paths = [Path("a.py"), Path("b.py")]
        self.assertEqual(
            argv_budget.batch_paths(paths, prefix_chars=10, limit=1000, headroom=0),
            [paths],
        )

    def test_an_overflowing_list_is_split(self) -> None:
        batches = argv_budget.batch_paths(
            self._wide(50), prefix_chars=20, limit=300, headroom=0
        )
        self.assertGreater(len(batches), 1)

    def test_every_batch_fits_the_budget(self) -> None:
        limit, headroom, prefix = 300, 20, 20
        for batch in argv_budget.batch_paths(
            self._wide(50), prefix_chars=prefix, limit=limit, headroom=headroom
        ):
            width = prefix + argv_budget.rendered_length([str(p) for p in batch])
            self.assertLessEqual(width, limit - headroom)

    def test_no_path_is_dropped_or_reordered(self) -> None:
        paths = self._wide(50)
        batches = argv_budget.batch_paths(paths, prefix_chars=20, limit=300, headroom=0)
        self.assertEqual([p for batch in batches for p in batch], paths)

    def test_a_path_wider_than_the_budget_gets_its_own_batch(self) -> None:
        """Never silently skip a file: an unfittable path is isolated, not dropped."""
        huge = Path("z" * 500 + ".py")
        paths = [Path("a.py"), huge, Path("b.py")]
        batches = argv_budget.batch_paths(paths, prefix_chars=10, limit=100, headroom=0)
        self.assertIn([huge], batches)
        self.assertEqual([p for batch in batches for p in batch], paths)

    def test_the_split_lands_exactly_at_the_boundary(self) -> None:
        """One path past the budget starts a NEW batch; it does not stretch the old one.

        The off-by-one matters in the unforgiving direction: a batch allowed to stretch
        by one more path is a batch over the ceiling it was sized against.
        """
        per = argv_budget.rendered_length([str(self._wide(1)[0])]) + 1
        for count, expected in ((4, [4]), (5, [4, 1]), (10, [4, 4, 2])):
            with self.subTest(count=count):
                batches = argv_budget.batch_paths(
                    self._wide(count),
                    prefix_chars=0,
                    limit=per * 4,
                    headroom=0,
                )
                self.assertEqual([len(b) for b in batches], expected)

    def test_empty_input_yields_no_batches(self) -> None:
        self.assertEqual(argv_budget.batch_paths([], prefix_chars=10, limit=100), [])

    def test_headroom_and_prefix_each_shrink_the_budget(self) -> None:
        paths = self._wide(30)
        base = argv_budget.batch_paths(paths, prefix_chars=0, limit=2000, headroom=0)
        with_headroom = argv_budget.batch_paths(
            paths, prefix_chars=0, limit=2000, headroom=1000
        )
        with_prefix = argv_budget.batch_paths(
            paths, prefix_chars=1000, limit=2000, headroom=0
        )
        self.assertGreater(len(with_headroom), len(base))
        self.assertGreater(len(with_prefix), len(base))

    def test_the_render_callable_is_what_gets_measured(self) -> None:
        """markdownlint's ':' literal-path token is wider than the bare path it wraps.

        Measuring the bare path while emitting the prefixed one would size every batch
        against a command line shorter than the one actually built.
        """
        paths = self._wide(30)
        bare = argv_budget.batch_paths(paths, prefix_chars=0, limit=400, headroom=0)
        prefixed = argv_budget.batch_paths(
            paths,
            prefix_chars=0,
            limit=400,
            headroom=0,
            render=lambda p: f":{p.as_posix()}",
        )
        self.assertGreaterEqual(len(prefixed), len(bare))
        for batch in prefixed:
            rendered = subprocess.list2cmdline([f":{p.as_posix()}" for p in batch])
            self.assertLessEqual(len(rendered), 400)

    def test_space_bearing_paths_are_measured_with_their_quoting(self) -> None:
        """The regression this pins: 40 quoted paths at a 400-char budget hit 419."""
        paths = [Path(f"C:/dir with spaces/f{i:04d}.py") for i in range(40)]
        for batch in argv_budget.batch_paths(
            paths, prefix_chars=0, limit=400, headroom=0
        ):
            rendered = subprocess.list2cmdline([str(p) for p in batch])
            self.assertLessEqual(len(rendered), 400)

    def test_the_measured_staged_tree_shape_is_split(self) -> None:
        """The shape that broke: ~325 files whose absolute paths average ~100 chars."""
        stem = "s" * 60
        paths = [
            Path(f"C:/staged/kit/utilities/tool{i:03d}/{stem}.py") for i in range(325)
        ]
        batches = argv_budget.batch_paths(
            paths,
            prefix_chars=40,
            limit=argv_budget.CREATE_PROCESS_ARGV_LIMIT,
            headroom=2048,
        )
        self.assertGreater(len(batches), 1)


if __name__ == "__main__":
    unittest.main()
