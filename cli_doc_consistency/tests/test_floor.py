"""Grouped suite: Check C, the stated version floor against the gated imports.

Three layers of one check: the imports a module makes and the floor they imply (including a
backport that lowers it again), where a floor is claimed (module docstring, parser epilog,
a formatter subclass), and the comparison that turns a mismatch into a finding. `_class_def`
lives here rather than in the shared fixtures because this is its only caller.
"""

from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import cli_doc_consistency as mod  # noqa: E402 - import follows the sys.path bootstrap above
from _fixtures import first_call, parse_module  # noqa: E402 - beside this module, via the loader's top_level_dir


def _class_def(source: str) -> ast.ClassDef:
    """The first class definition in *source* (mypy-narrowed)."""
    node = parse_module(source).body[0]
    assert isinstance(node, ast.ClassDef)
    return node


class ImportsAndFloorTest(unittest.TestCase):
    def test_top_module(self) -> None:
        self.assertEqual(mod._top_module("a.b.c"), "a")
        self.assertEqual(mod._top_module("x"), "x")

    def test_collect_imports_scopes(self) -> None:
        tree = parse_module(
            """
            import tomllib
            from a.b import c
            from . import rel
            def f():
                import zoneinfo
            """
        )
        hard, every = mod.collect_imports(tree)
        self.assertIn("tomllib", hard)
        self.assertIn("a", hard)
        self.assertNotIn("zoneinfo", hard)  # nested import is not a hard floor
        self.assertIn("zoneinfo", every)

    def test_implied_floor_tomllib(self) -> None:
        self.assertEqual(mod.implied_floor({"tomllib"}, {"tomllib"}), (3, 11))

    def test_implied_floor_backport_neutralizes(self) -> None:
        self.assertIsNone(mod.implied_floor({"tomllib"}, {"tomllib", "tomli"}))

    def test_implied_floor_zoneinfo_no_backport(self) -> None:
        self.assertEqual(mod.implied_floor({"zoneinfo"}, {"zoneinfo"}), (3, 9))

    def test_implied_floor_none_when_ungated(self) -> None:
        self.assertIsNone(mod.implied_floor({"os", "sys"}, {"os", "sys"}))

    def test_implied_floor_max_of_several(self) -> None:
        self.assertEqual(
            mod.implied_floor({"tomllib", "zoneinfo"}, {"tomllib", "zoneinfo"}), (3, 11)
        )

    def test_version_str(self) -> None:
        self.assertEqual(mod._version_str((3, 11)), "3.11")


class FloorClaimLocationTest(unittest.TestCase):
    def test_leading_docstring_variants(self) -> None:
        self.assertEqual(
            mod._leading_docstring(parse_module('"""doc"""\nx=1').body), ("doc", 1)
        )
        self.assertIsNone(
            mod._leading_docstring(parse_module("x=1").body)
        )  # not an Expr
        self.assertIsNone(mod._leading_docstring([]))  # empty body
        self.assertIsNone(
            mod._leading_docstring(parse_module("42").body)
        )  # Expr, non-str

    def test_names_argument_parser(self) -> None:
        # The one predicate both the CLI signal and this floor check use.
        for src in (
            "argparse.ArgumentParser()",
            "ArgumentParser()",
            "stdio_utf8.EscapingArgumentParser()",
        ):
            with self.subTest(src=src):
                self.assertTrue(mod.names_argument_parser(first_call(src).func))
        self.assertFalse(mod.names_argument_parser(first_call("foo()").func))

    def test_an_escaping_parser_epilog_is_read(self) -> None:
        # Its floor claim must count exactly as argparse.ArgumentParser's does.
        tree = parse_module(
            'import stdio_utf8\nstdio_utf8.EscapingArgumentParser(epilog="3.9+")'
        )
        self.assertIsInstance(mod._epilog_node(tree), ast.Constant)

    def test_is_formatter_class(self) -> None:
        self.assertTrue(mod._is_formatter_class(_class_def("class MyFormatter: pass")))
        self.assertTrue(
            mod._is_formatter_class(_class_def("class C(argparse.HelpFormatter): pass"))
        )
        self.assertTrue(
            mod._is_formatter_class(_class_def("class C(RawFormatter): pass"))
        )
        self.assertFalse(mod._is_formatter_class(_class_def("class C(object): pass")))

    def test_epilog_node_present_absent_and_no_parser(self) -> None:
        with_epilog = parse_module(
            'import argparse\nargparse.ArgumentParser(epilog="Python 3.9+")'
        )
        no_epilog = parse_module(
            "import argparse\nargparse.ArgumentParser(description='d')"
        )
        no_parser = parse_module("x = 1")
        self.assertIsInstance(mod._epilog_node(with_epilog), ast.Constant)
        self.assertIsNone(mod._epilog_node(no_epilog))
        self.assertIsNone(mod._epilog_node(no_parser))

    def test_floor_claim_texts_collects_canonical(self) -> None:
        tree = parse_module(
            '''
            """Module needs 3.10+."""
            import argparse
            class F(argparse.ArgumentDefaultsHelpFormatter):
                """target 3.11+ floor."""
            argparse.ArgumentParser(epilog="Runs on 3.9+.")
            '''
        )
        joined = " ".join(text for text, _line in mod._floor_claim_texts(tree))
        self.assertIn("3.10+", joined)
        self.assertIn("3.11+", joined)
        self.assertIn("3.9+", joined)

    def test_floor_claim_texts_formatter_without_docstring(self) -> None:
        # A formatter class with no leading docstring is skipped (class_doc is None).
        tree = parse_module(
            '''
            """mod doc"""
            import argparse
            class F(argparse.HelpFormatter):
                attr = 1
            '''
        )
        self.assertEqual([t for t, _ in mod._floor_claim_texts(tree)], ["mod doc"])

    def test_joinedstr_epilog_not_collected(self) -> None:
        # An f-string epilog is not a Constant, so it is not scanned (no crash).
        tree = parse_module(
            'import argparse\nx=1\nargparse.ArgumentParser(epilog=f"3.9+ {x}")'
        )
        self.assertEqual(mod._floor_claim_texts(tree), [])


class CheckFloorTest(unittest.TestCase):
    def test_no_gated_import_is_clean(self) -> None:
        tree = parse_module('"""Says 3.9+ but imports nothing gated."""\nimport os')
        self.assertEqual(mod.check_floor(tree, Path("t.py"), Path("t.md"), None), [])

    def test_py_floor_below_implied_flags(self) -> None:
        tree = parse_module('"""Needs 3.10+ only."""\nimport tomllib')
        findings = mod.check_floor(tree, Path("t.py"), Path("t.md"), None)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].check, mod.CHECK_C)
        self.assertEqual(findings[0].file, "t.py")

    def test_md_floor_below_implied_flags(self) -> None:
        tree = parse_module('"""ok 3.11+."""\nimport tomllib')
        findings = mod.check_floor(
            tree, Path("t.py"), Path("t.md"), "Requires Python 3.10+ here.\n"
        )
        self.assertEqual([f.file for f in findings], ["t.md"])

    def test_stated_floors_raw_lines(self) -> None:
        claims = mod.stated_floors("line one 3.9+\nline two\n3.13+ here\n")
        self.assertEqual([(c[0], c[1]) for c in claims], [(1, (3, 9)), (3, (3, 13))])
