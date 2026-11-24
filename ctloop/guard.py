"""Anti-weakening guard (D6): cheap AST checks on every candidate test.

It catches the cheap ways a model can make a failing test "pass" without testing
anything. It can't catch a changed expected value; that residual risk is covered by
the TARGET_BUG verdict and human review of pr.md (documented in the README).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field

_SKIP_ATTRS = {"skip", "skipif", "xfail", "importorskip"}


@dataclass
class GuardResult:
    """ok is False if any rule failed; reasons lists every failed rule."""

    ok: bool
    reasons: list[str] = field(default_factory=list)
    assertions: int = 0


def count_assertions(tree: ast.AST) -> int:
    """Number of assert statements plus pytest.raises uses."""
    count = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Assert):
            count += 1
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "raises":
            count += 1
    return count


def _imports_module(tree: ast.AST, module: str) -> bool:
    package, _, leaf = module.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module == module or node.module.startswith(module + "."):
                return True
            if package and node.module == package and any(a.name == leaf for a in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name == module for a in node.names):
            return True
    return False


def check_candidate(code: str, module: str, previous: str | None = None) -> GuardResult:
    """Check a candidate test module.

    Args:
        code: the candidate test source.
        module: dotted module under test; the test must import it.
        previous: the prior version (for fixes); the assertion count must not drop.

    Returns:
        GuardResult with ok and the reasons for any rejection.
    """
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        return GuardResult(False, [f"does not parse: {exc.msg} (line {exc.lineno})"])
    reasons: list[str] = []
    if not _imports_module(tree, module):
        reasons.append(f"does not import {module}")
    asserts = count_assertions(tree)
    if asserts < 1:
        reasons.append("has no assertions")
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _SKIP_ATTRS:
            reasons.append(f"uses {node.attr}")
        elif isinstance(node, ast.Assert) and isinstance(node.test, ast.Constant) and node.test.value:
            reasons.append("uses assert with a constant truthy value")
        elif isinstance(node, ast.ExceptHandler):
            names = {getattr(n, "id", getattr(n, "attr", "")) for n in
                     (node.type.elts if isinstance(node.type, ast.Tuple) else [node.type])} if node.type else set()
            if node.type is None or names & {"AssertionError", "BaseException"}:
                reasons.append("catches assertion failures (bare except / AssertionError)")
    if previous is not None:
        try:
            before = count_assertions(ast.parse(previous))
        except SyntaxError:
            before = 0
        if asserts < before:
            reasons.append(f"assertion count dropped ({before} -> {asserts})")
    return GuardResult(not reasons, sorted(set(reasons)), asserts)
