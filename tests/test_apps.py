"""The app files are never imported by the rest of the suite.

That gap shipped a real defect: a syntax error in app_v4.py sat behind 60
passing tests, because nothing in tests/ touches the Streamlit entry points.
Compiling them is cheap and catches the whole class.
"""

from __future__ import annotations

import ast
import py_compile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
APPS = sorted(ROOT.glob("app*.py"))


def test_there_are_app_files_to_check():
    # A glob that silently matches nothing would make every test below vacuous.
    assert APPS, "no app*.py found; this suite would pass on an empty set"


@pytest.mark.parametrize("path", APPS, ids=lambda p: p.name)
def test_app_compiles(path):
    py_compile.compile(str(path), doraise=True)


@pytest.mark.parametrize("path", APPS, ids=lambda p: p.name)
def test_app_imports_resolve_within_the_package(path):
    """Every `from bipp import X` names a module that exists.

    Catches a rename in bipp/ that leaves an app pointing at nothing, which the
    compile check above cannot see.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "bipp":
            for alias in node.names:
                assert (ROOT / "bipp" / f"{alias.name}.py").exists(), \
                    f"{path.name} imports bipp.{alias.name}, which does not exist"
