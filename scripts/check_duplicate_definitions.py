#!/usr/bin/env python
"""Static duplicate function/method definition detector (Track A / A0.4).

Purpose
-------
Detect accidental duplicate function or method definitions at the *same*
scope. This catches the class of drift introduced during long agent
sessions where an incomplete stub is left in place and a complete
implementation is added later in the same class/module body, silently
shadowing the first definition.

Heuristic
---------
A definition is considered a duplicate only when two ``def``/``async def``
statements with the same name appear as *direct siblings* in the same body
list (module body or class body). Definitions that live in mutually
exclusive branches (e.g. ``if sys.platform == ...: def f() ... else: def f()``)
are intentionally NOT flagged, because they are legitimate conditional
definitions, not drift.

Nested functions live in their own scope and are therefore never compared
against their enclosing scope.

Usage
-----
    python scripts/check_duplicate_definitions.py [paths...]

Exit code is non-zero when duplicates are found so it can be used as a
lightweight CI / pre-commit gate.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import Iterable, List, NamedTuple, Optional

DEFAULT_PATHS = ("core", "adapters", "tests")


class DuplicateDefinition(NamedTuple):
    file: str
    scope: str
    name: str
    lines: List[int]

    def __str__(self) -> str:
        locs = ", ".join(f"line {ln}" for ln in self.lines)
        return f"{self.file}: scope '{self.scope}' defines '{self.name}' {len(self.lines)}x ({locs})"


def _scan_body(body: List[ast.stmt], file: str, scope: str,
               results: List[DuplicateDefinition]) -> None:
    """Scan a single body list for duplicate direct-child definitions."""
    seen: dict[str, List[int]] = {}
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            seen.setdefault(node.name, []).append(node.lineno)

    for name, lines in seen.items():
        if len(lines) > 1:
            results.append(
                DuplicateDefinition(
                    file=file,
                    scope=scope,
                    name=name,
                    lines=sorted(lines),
                )
            )

    # Recurse into nested scopes (classes and nested functions).
    for node in body:
        if isinstance(node, ast.ClassDef):
            _scan_body(
                node.body,
                file,
                f"{scope}.{node.name}" if scope else node.name,
                results,
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            _scan_body(
                node.body,
                file,
                f"{scope}.{node.name}()" if scope else f"{node.name}()",
                results,
            )


def find_duplicate_definitions_in_source(
    source: str, file: str
) -> List[DuplicateDefinition]:
    """Return duplicate definitions found in a single source string."""
    results: List[DuplicateDefinition] = []
    try:
        tree = ast.parse(source, filename=file)
    except SyntaxError:
        # Unparseable files are reported elsewhere by pytest; skip here.
        return results
    _scan_body(tree.body, file, "<module>", results)
    return results


def _iter_python_files(paths: Iterable[str]) -> Iterable[Path]:
    for raw in paths:
        p = Path(raw)
        if p.is_file() and p.suffix == ".py":
            yield p
        elif p.is_dir():
            for f in sorted(p.rglob("*.py")):
                # Skip vendored / generated trees.
                if any(part in {"__pycache__", ".venv", "venv",
                                "mcp_server"} for part in f.parts):
                    continue
                yield f


def find_duplicate_definitions(paths: Iterable[str]) -> List[DuplicateDefinition]:
    """Scan the given files/directories for duplicate definitions."""
    results: List[DuplicateDefinition] = []
    for path in _iter_python_files(paths):
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        results.extend(
            find_duplicate_definitions_in_source(source, str(path))
        )
    return results


def main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    paths = args if args else list(DEFAULT_PATHS)
    duplicates = find_duplicate_definitions(paths)

    if not duplicates:
        print(f"No duplicate definitions found in: {', '.join(paths)}")
        return 0

    print(f"Found {len(duplicates)} duplicate definition(s):")
    for dup in duplicates:
        print(f"  - {dup}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
