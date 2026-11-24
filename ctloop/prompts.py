"""Prompt building and model-output parsing.

A3: prompts carry only content from inside the target copy (function source, module
header, one existing test as a style example, missing lines), capped at about 8k chars.
Each prompt starts with a machine-readable CTLOOP-KEY line so the stub provider can map
it to a canned response deterministically.
"""

from __future__ import annotations

import re

MAX_CONTEXT_CHARS = 8000
TRUNCATED = "\n# ... [truncated by ctloop to stay within the context cap] ...\n"

SYSTEM_PROMPT = """You write pytest unit tests for an existing Python package.
Rules:
- Reply with exactly one fenced ```python code block containing a complete test module.
- Import the function under test from its real module. Never redefine it.
- The function's docstring is the specification. Assert the documented behavior.
- Never change an expected value to match observed output. Never skip, xfail, or catch AssertionError.
- If the failure shows the function contradicts its docstring, reply with
  `VERDICT: TARGET_BUG <one-line reason>` instead of code."""


def _cap(text: str, limit: int = MAX_CONTEXT_CHARS) -> str:
    return text if len(text) <= limit else text[:limit] + TRUNCATED


def build_write_prompt(*, key: str, module: str, qualname: str, source: str, header: str,
                       missing_lines: list[int], example_test: str) -> str:
    """Prompt asking for a new test module covering ``qualname``."""
    context = _cap(
        f"Module `{module}` (imports and signatures):\n```python\n{header}\n```\n\n"
        f"Function under test `{module}.{qualname}`:\n```python\n{source}\n```\n\n"
        f"Untested line numbers: {missing_lines}\n\n"
        f"Existing test (style example):\n```python\n{example_test}\n```\n"
    )
    return (
        f"CTLOOP-KEY: {key}\n"
        f"Write a pytest module that tests `{qualname}` from `{module}` and covers the untested lines.\n\n"
        f"{context}"
    )


def build_fix_prompt(*, key: str, write_prompt: str, test_code: str, failure_tail: str) -> str:
    """Prompt asking to fix a failing test (never the target)."""
    body = write_prompt.split("\n", 1)[1] if write_prompt.startswith("CTLOOP-KEY:") else write_prompt
    return (
        f"CTLOOP-KEY: {key}\n"
        "The test below fails. Fix the TEST so it asserts the documented behavior. "
        "Do not weaken assertions. If the function itself contradicts its docstring, reply with "
        "`VERDICT: TARGET_BUG <reason>`.\n\n"
        f"Current test:\n```python\n{_cap(test_code, 4000)}\n```\n\n"
        f"Failure output (last 40 lines):\n```\n{failure_tail}\n```\n\n"
        f"Original task:\n{_cap(body)}"
    )


_FENCE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*\n(.*?)```", re.DOTALL)


def extract_code(text: str) -> str | None:
    """Return the last ```python fenced block (or the last fenced block of any kind).

    The last block is used because models sometimes show a snippet before the final module.
    Returns None if there is no fenced block.
    """
    blocks = _FENCE.findall(text)
    if not blocks:
        return None
    python = [body for lang, body in blocks if lang.lower() in ("python", "py")]
    chosen = (python or [body for _, body in blocks])[-1]
    return chosen.rstrip() + "\n"


_VERDICT = re.compile(r"VERDICT:\s*TARGET_BUG\b[ \t:-]*(.*)")


def parse_verdict(text: str) -> str | None:
    """Return the model's reason if it declared a target bug, else None."""
    match = _VERDICT.search(text)
    if match is None:
        return None
    return match.group(1).strip() or "model reported a target bug"
