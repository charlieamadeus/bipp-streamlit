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


def test_deployment_check_passes_against_the_real_package():
    """If this fails, the app names a symbol bipp/ does not export."""
    import app_v4
    assert app_v4.check_deployment() == []


def test_deployment_check_names_what_is_missing():
    import app_v4
    module, names = app_v4.REQUIRED["bipp.ccir"]

    class Hollow:
        pass

    app_v4.REQUIRED["bipp.ccir"] = (Hollow(), names)
    try:
        missing = app_v4.check_deployment()
        assert missing, "a module with none of the required names must report as stale"
        assert all(m.startswith("bipp.ccir.") for m in missing)
    finally:
        app_v4.REQUIRED["bipp.ccir"] = (module, names)


def test_headline_hours_keeps_daily_moves_visible():
    """Two-sig-fig rounding collapsed a week of real variation into one label."""
    import app_v4

    assert app_v4._two_figures(17_043) == "17,000"
    assert app_v4._two_figures(17_467) == "17,000"
    assert app_v4._headline_hours(17_043) == "17,043"
    assert app_v4._headline_hours(17_467) == "17,467"
