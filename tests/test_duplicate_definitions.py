"""Regression tests for the duplicate-definition detector (Track A / A0.4).

These tests cover the exact class of agent-session drift identified during
A0: an incomplete stub left in place and a complete implementation added
later at the same scope, silently shadowing the first definition.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.check_duplicate_definitions import (  # noqa: E402
    find_duplicate_definitions_in_source,
    find_duplicate_definitions,
)


def test_detects_duplicate_method_in_class():
    source = (
        "class A:\n"
        "    def f(self):\n"
        "        return 1\n"
        "    def f(self):\n"
        "        return 2\n"
    )
    dups = find_duplicate_definitions_in_source(source, "sample.py")
    assert len(dups) == 1
    assert dups[0].name == "f"
    assert dups[0].scope == "<module>.A"
    assert dups[0].lines == [2, 4]


def test_detects_duplicate_module_function():
    source = (
        "def g():\n"
        "    pass\n"
        "def g():\n"
        "    pass\n"
    )
    dups = find_duplicate_definitions_in_source(source, "sample.py")
    assert len(dups) == 1
    assert dups[0].name == "g"
    assert dups[0].scope == "<module>"


def test_does_not_flag_conditional_platform_definitions():
    """Mutually-exclusive branch defs are legitimate, not drift."""
    source = (
        "import sys\n"
        "if sys.platform == 'win32':\n"
        "    def f():\n"
        "        return 'win'\n"
        "else:\n"
        "    def f():\n"
        "        return 'posix'\n"
    )
    dups = find_duplicate_definitions_in_source(source, "sample.py")
    assert dups == []


def test_does_not_flag_nested_function_with_same_name():
    """A nested def lives in its own scope; not a sibling duplicate."""
    source = (
        "def outer():\n"
        "    def helper():\n"
        "        return 1\n"
        "    return helper\n"
        "def helper():\n"
        "    return 2\n"
    )
    dups = find_duplicate_definitions_in_source(source, "sample.py")
    assert dups == []


def test_does_not_flag_distinct_names():
    source = (
        "class A:\n"
        "    def f(self):\n"
        "        pass\n"
        "    def g(self):\n"
        "        pass\n"
    )
    dups = find_duplicate_definitions_in_source(source, "sample.py")
    assert dups == []


def test_detects_async_def_duplicate():
    source = (
        "class A:\n"
        "    async def f(self):\n"
        "        pass\n"
        "    async def f(self):\n"
        "        pass\n"
    )
    dups = find_duplicate_definitions_in_source(source, "sample.py")
    assert len(dups) == 1
    assert dups[0].name == "f"


def test_syntax_error_is_ignored():
    dups = find_duplicate_definitions_in_source("def (:\n", "broken.py")
    assert dups == []


def test_repository_core_is_clean():
    """The hardened core/ tree must not contain same-scope duplicates."""
    root = Path(__file__).resolve().parent.parent
    dups = find_duplicate_definitions([str(root / "core")])
    assert dups == [], f"duplicate definitions in core/: {dups}"
