"""Turn coverage.py JSON into a list of gaps: functions that have untested lines.

coverage's per-function data (files[*].functions) already maps missing lines to
qualified names, so ast is only used for the A2 prefilter (skip code that can't be
meaningfully tested, so no model call is wasted on it) and to pull function source.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Gap:
    """One function with missing lines."""

    file: str
    module: str
    qualname: str
    start_line: int
    missing_lines: tuple[int, ...]

    @property
    def slug(self) -> str:
        """Stable id used for test file names and stub keys, e.g. strings_truncate."""
        mod = self.module.split(".", 1)[1] if "." in self.module else self.module
        return f"{mod}_{self.qualname}".replace(".", "_")


def module_name(rel_file: str) -> str:
    """sandbox/strings.py -> sandbox.strings (and pkg/__init__.py -> pkg)."""
    parts = Path(rel_file).with_suffix("").parts
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def find_function(tree: ast.Module, qualname: str) -> tuple[ast.FunctionDef | ast.AsyncFunctionDef | None, ast.ClassDef | None]:
    """Find a (possibly nested-in-class) function by dotted qualname. Returns (func, enclosing class)."""
    scope: list[ast.stmt] = tree.body
    owner: ast.ClassDef | None = None
    names = qualname.split(".")
    for i, name in enumerate(names):
        match = next((n for n in scope if getattr(n, "name", None) == name), None)
        if match is None:
            return None, None
        if i == len(names) - 1:
            if isinstance(match, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return match, owner
            return None, None
        if not isinstance(match, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            return None, None
        owner = match if isinstance(match, ast.ClassDef) else owner
        scope = match.body
    return None, None


def _is_trivial_body(func: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    body = list(func.body)
    if body and isinstance(body[0], ast.Expr) and isinstance(getattr(body[0], "value", None), ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # docstring
    return all(isinstance(s, ast.Pass) or (isinstance(s, ast.Expr) and isinstance(s.value, ast.Constant)
                                           and s.value.value is Ellipsis) for s in body)


def _decorator_names(func: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = set()
    for d in func.decorator_list:
        node = d.func if isinstance(d, ast.Call) else d
        names.add(node.attr if isinstance(node, ast.Attribute) else getattr(node, "id", ""))
    return names


def _is_protocol(cls: ast.ClassDef | None) -> bool:
    if cls is None:
        return False
    return any((isinstance(b, ast.Name) and b.id == "Protocol") or
               (isinstance(b, ast.Attribute) and b.attr == "Protocol") for b in cls.bases)


def untestable_reason(func: ast.FunctionDef | ast.AsyncFunctionDef, cls: ast.ClassDef | None) -> str | None:
    """A2 prefilter: return why this function isn't worth a model call, or None."""
    if "abstractmethod" in _decorator_names(func):
        return "abstract method"
    if _is_protocol(cls):
        return "Protocol class body"
    if _is_trivial_body(func):
        return "empty body (pass/...)"
    return None


def _main_block_lines(tree: ast.Module) -> set[int]:
    lines: set[int] = set()
    for node in tree.body:
        if isinstance(node, ast.If) and isinstance(node.test, ast.Compare) \
                and isinstance(node.test.left, ast.Name) and node.test.left.id == "__name__":
            lines.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return lines


def parse_gaps(cov: dict[str, Any], work: Path) -> tuple[list[Gap], list[dict[str, Any]]]:
    """Extract gaps from coverage JSON, in deterministic (file, line) order.

    Args:
        cov: the parsed coverage JSON (format 3, with per-function data).
        work: the copy root the coverage paths are relative to (used to read source).

    Returns:
        (gaps that deserve a test, untestable entries reported as skipped with a reason).
    """
    gaps: list[Gap] = []
    untestable: list[dict[str, Any]] = []
    for rel in sorted(cov.get("files", {})):
        data = cov["files"][rel]
        source_path = work / rel
        tree = ast.parse(source_path.read_text(encoding="utf-8")) if source_path.exists() else None
        for qualname, fdata in sorted(data.get("functions", {}).items(), key=lambda kv: kv[1].get("start_line") or 0):
            missing = list(fdata.get("missing_lines", []))
            if not missing:
                continue
            if qualname == "":
                # Module-level code: only report it when it's a __main__ block (A2).
                if tree is not None and set(missing) <= _main_block_lines(tree):
                    untestable.append({"file": rel, "qualname": "<module>", "reason": "__main__ block"})
                continue
            if tree is not None:
                func, cls = find_function(tree, qualname)
                reason = untestable_reason(func, cls) if func else None
                if reason:
                    untestable.append({"file": rel, "qualname": qualname, "reason": reason})
                    continue
            gaps.append(Gap(rel, module_name(rel), qualname, int(fdata.get("start_line") or 0), tuple(missing)))
    return gaps, untestable


def function_source(work: Path, rel_file: str, qualname: str) -> str:
    """Return the full source (with decorators and docstring) of ``qualname`` in ``rel_file``."""
    text = (work / rel_file).read_text(encoding="utf-8")
    func, _ = find_function(ast.parse(text), qualname)
    if func is None:
        return ""
    start = min([d.lineno for d in func.decorator_list] + [func.lineno])
    return "\n".join(text.splitlines()[start - 1: func.end_lineno])


def module_header(work: Path, rel_file: str) -> str:
    """The module's import block plus one-line signatures of its top-level functions (A3 context)."""
    text = (work / rel_file).read_text(encoding="utf-8")
    tree = ast.parse(text)
    lines = text.splitlines()
    out: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            out.extend(lines[node.lineno - 1: node.end_lineno])
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            out.append(lines[node.lineno - 1])
    return "\n".join(out)
