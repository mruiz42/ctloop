"""PR folder writers: changes.patch (new test files only) and pr.md."""

from __future__ import annotations

import difflib
from collections.abc import Iterable

from ctloop.state import RunState, Status


def build_patch(files: Iterable[tuple[str, str]]) -> str:
    """Unified diff that creates each (relative path, content) file.

    Uses the `--- /dev/null` / `+++ b/<path>` form that `patch -p1` accepts for new
    files. A trailing newline is guaranteed because difflib can't emit the
    "No newline at end of file" marker.
    """
    chunks: list[str] = []
    for rel, content in files:
        if not content.endswith("\n"):
            content += "\n"
        diff = difflib.unified_diff([], content.splitlines(keepends=True), "/dev/null", f"b/{rel}")
        chunks.append("".join(diff))
    return "".join(chunks)


def _fmt_cov(cov: dict | None) -> str:
    if not cov:
        return "n/a"
    return f"{cov['percent']}% (lines + branches; statements {cov['percent_statements']}%)"


def render_pr_md(state: RunState, *, integrity_ok: bool, file_count: int) -> str:
    """Render pr.md from the final state."""
    cfg = state.config
    passed = [t for t in state.tests if t.status == Status.PASSED]
    quarantined = [t for t in state.tests if t.status == Status.QUARANTINED]
    skipped = [t for t in state.tests if t.status == Status.SKIPPED]
    lines = [
        f"# ctloop PR: add tests for `{state.package}`",
        "",
        "## Summary",
        f"- Run: `{state.run_id}`",
        f"- Provider: {cfg['provider']}" + (f" (model `{cfg['model']}`)" if cfg["provider"] == "claude" else ""),
        f"- Model calls: {state.calls_used} / {cfg['max_calls']}",
        f"- Spend: ${state.cost_usd:.4f} / ${cfg['max_run_usd']:.2f} (per call cap ${cfg['max_budget_usd']:.2f})",
        f"- claude --safe-mode: {'on' if state.safe_mode_supported else 'FELL BACK to cwd isolation only'}",
        f"- Test sandbox: {'sandbox-exec (no network, writes only in the run folder)' if not cfg['unsafe_no_sandbox'] else 'DISABLED (--unsafe-no-sandbox)'}",
        f"- Stop reason: **{state.stop_reason or 'completed'}**",
        "",
        "## Coverage",
        f"- Before: {_fmt_cov(state.baseline)}",
        f"- After:  {_fmt_cov(state.after)}",
        f"- Combined suite in verify/ (patch applied via {state.patch_method}): "
        f"{'PASS' if state.after_suite_ok else 'FAIL'}",
        "",
    ]
    if state.baseline and state.after:
        lines += ["| File | Before | After |", "|---|---|---|"]
        for f, after in state.after["files"].items():
            lines.append(f"| `{f}` | {state.baseline['files'].get(f, 'n/a')}% | {after}% |")
        lines.append("")
    lines += ["## Tests added", ""]
    lines += [f"- `{t.test_file}` -> `{t.module}.{t.qualname}` (fix attempts: {t.attempts})" for t in passed] or ["- none"]
    lines += ["", "## Quarantined: suspected target bugs", ""]
    if not quarantined:
        lines.append("- none")
    for t in quarantined:
        lines += [f"### `{t.module}.{t.qualname}`", f"- Fix attempts: {t.attempts}", f"- Reason: {t.reason}",
                  "- Last failure output:", "", "```", t.failure_tail.rstrip(), "```", ""]
    lines += ["", "## Skipped", ""]
    lines += [f"- `{t.module}.{t.qualname}`: {t.reason}" for t in skipped]
    lines += [f"- `{u['file']}::{u['qualname']}`: untestable ({u['reason']})" for u in state.untestable]
    if not skipped and not state.untestable:
        lines.append("- none")
    rejections = [(t, h) for t in state.tests for h in t.history if h.startswith("guard rejected")]
    lines += ["", "## Guard rejections", ""]
    lines += [f"- `{t.qualname}`: {h}" for t, h in rejections] or ["- none"]
    if state.warnings:
        lines += ["", "## Warnings", ""] + [f"- {w}" for w in state.warnings]
    lines += [
        "", "## Target integrity", "",
        f"- {'PASS' if integrity_ok else 'FAIL'}: {file_count} files, sha256 `{state.target_hash}`",
        "", "## How to apply", "", "```", f"patch -p1 -d {state.target} < pr/changes.patch", "```", "",
    ]
    return "\n".join(lines)
