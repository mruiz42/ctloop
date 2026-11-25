# 01 — Investigation: Converging Test Loop

Investigated 2026-11-24, about 14:06 to 14:10. Lean by design (60-minute project). Everything marked "verified locally" was run in a scratchpad venv outside the project. The project folder has no changes apart from this doc.

## Open questions
None blocking. The one minor point (which model alias to default to for `claude -p`) is listed under Options for the planner.

## Scope investigated
1. How to call `claude -p` headless: the flags for non-interactive, text-only, structured output, the limits, and calling it safely from subprocess.
2. How to read coverage gaps programmatically: coverage.py JSON fields, and mapping missing lines to functions.
3. How to run pytest + coverage on an isolated copy with timeouts, and run each generated test file alone to tell pass from fail.
4. How to make a patch without git: difflib unified_diff for new files.
5. Whether pytest and coverage install with uv on Python 3.15.2.
6. Risks: 3.15 compatibility, latency and cost against the 30-call cap, nondeterminism, keeping the target untouched.

## Findings

### 1. `claude -p` headless usage (confidence: high, verified locally on Claude Code 2.1.331)
- **Smoke call (one call, about $0.012, 1.3 s wall time):**
  `claude -p --output-format json --tools "" --no-session-persistence --model sonnet --max-budget-usd 0.05 "Reply with exactly: OK" < /dev/null`
  It exited with rc=0, and stdout was a single JSON object. Useful keys: `result` (the text, here `"OK"`), `is_error` (bool), `subtype` (`"success"`), `num_turns`, `total_cost_usd`, `duration_ms`, `stop_reason`, `session_id`, `api_error_status`.
- **Flags that matter** (from `claude --help` plus the CLI reference):
  - `-p/--print`: non-interactive mode. The prompt can be the positional arg or **stdin** (`cat file | claude -p "query"`). For long prompts that contain source code, stdin avoids argv length and quoting problems.
  - `--output-format json`: a single result object. `stream-json` isn't needed here.
  - `--tools ""`: **turns off all built-in tools**, so the model can only return text and can't touch the filesystem. Verified: the call worked and `permission_denials` was empty.
  - `--max-turns N`: caps agentic turns in print mode. It works but **isn't shown in local `--help`**. It's documented in the CLI reference. With tools off, one call is one turn, so `--max-turns 1` is just a belt-and-braces guard.
  - `--json-schema '<schema>'`: validated structured output in print mode. Not tested locally. Plain text with fenced-code extraction is the simpler fallback. (Medium confidence on how the output field is shaped. See https://code.claude.com/docs/en/agent-sdk/structured-outputs)
  - `--max-budget-usd X`: stops the call once its estimated spend reaches X. This is a client-side estimate and "can pass" the cap slightly.
  - `--no-session-persistence`: doesn't write session files.
  - `--model <alias|name>`: for example `sonnet` or `opus`.
  - `--system-prompt` / `--append-system-prompt`: sets the role and output contract.
  - `--safe-mode`: doesn't load CLAUDE.md, skills, plugins, hooks, or MCP. Auth works normally. This is useful so the project's own CLAUDE.md and agents don't leak into generation prompts. (Medium confidence. Read from docs and not tested.)
  - `--bare`: **avoid.** Auth is then strictly `ANTHROPIC_API_KEY` (OAuth and keychain are never read), and this machine uses a logged-in session.
- **There is no built-in timeout flag.** Enforce the timeout with `subprocess.run(..., timeout=T)` and catch `subprocess.TimeoutExpired`. On timeout, `subprocess.run` kills the child and waits for it.
- **Calling it safely from subprocess:** pass an **arg list** (never `shell=True`) and send the prompt via `input=` with `text=True` and `capture_output=True`. Then check `returncode`, `json.loads(stdout)`, `is_error`, and `subtype == "success"`. Run with `cwd=` set to a neutral directory (the run folder) so the CLI doesn't pick up the project's CLAUDE.md, or use `--safe-mode`. Use `shutil.which("claude")` to give a clear error when the CLI is missing.
- Sources: local `claude --help`, https://code.claude.com/docs/en/cli-reference, https://code.claude.com/docs/en/headless, https://docs.python.org/3/library/subprocess.html#subprocess.run

### 2. Coverage gaps from coverage.py JSON (confidence: high, verified locally with coverage 7.17.5)
- Commands: `python -m coverage run --branch --source=<pkg> -m pytest ...`, then `python -m coverage json -o cov.json -q`.
- Top-level structure: `meta` (`format: 3`, `version`, `branch_coverage`, `show_contexts`), `files`, and `totals`.
- Each entry in `files[<relpath>]` has `executed_lines`, `missing_lines`, `excluded_lines`, `summary`, plus `executed_branches` and `missing_branches` (pairs like `[[2, 4]]`) when you use `--branch`, plus **`functions` and `classes`**.
- `summary` / `totals` fields: `percent_covered` (float; this blends lines and branches when `--branch` is on), `percent_covered_display`, `covered_lines`, `num_statements`, `missing_lines`, `percent_statements_covered`, `num_branches`, `covered_branches`, `missing_branches`, `num_partial_branches`, `percent_branches_covered`.
- **`functions` already maps gaps to functions.** Each key is a qualified function name (and `""` holds module-level code). Each value has `start_line`, `executed_lines`, `missing_lines`, `missing_branches`, and its own `summary`. Verified: an uncalled `g()` showed `missing_lines: [6]` and `start_line: 5`. This means **`ast` isn't strictly needed to find which function has gaps**. `ast` is still handy for pulling out the function's source text (`ast.get_source_segment`, or the `lineno`/`end_lineno` of `FunctionDef`) to put in the prompt.
- Sources: https://coverage.readthedocs.io/en/latest/cmd.html#json-reporting-coverage-json, https://coverage.readthedocs.io/en/latest/changes.html (the per-function and per-class JSON data was added in 7.5.0, medium confidence on the exact version), https://docs.python.org/3/library/ast.html

### 3. Running pytest + coverage on an isolated copy (confidence: high)
- Copy the target with `shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", ".coverage*"))` into `runs/<run-id>/work/`, then run everything with `cwd=<copy>`.
- Use the **venv interpreter** explicitly (`sys.executable` or `.venv/bin/python`) with `-m pytest` / `-m coverage`. `-m` puts cwd on `sys.path`, so the copied package imports from the copy and not from the original.
- Useful pytest flags: `-q`, `-p no:cacheprovider` (no `.pytest_cache`), `-x` is optional, and `--rootdir=<copy>`. Set the env var `PYTHONDONTWRITEBYTECODE=1`.
- **Per-file isolation:** run `python -m pytest <copy>/tests/test_gen_x.py` as a separate subprocess with `timeout=`. **pytest exit codes:** 0 = all passed, 1 = some tests failed, 2 = interrupted, 3 = internal error, 4 = usage error, 5 = no tests collected. Treat anything other than 0 as "not green" and keep the stdout tail as the failure output for the fix prompt or the quarantine report. Exit codes 2 or 4 often mean a collection or import error in the generated file (for example a syntax error), and should count as a failure that gets fixed.
- **Final coverage:** after quarantining, run once more over all tests that were kept (existing plus generated) for the "after" number. The "before" number is the same command with only the existing tests.
- A pytest timeout plugin isn't needed, because the subprocess `timeout=` handles hangs.
- Sources: https://docs.pytest.org/en/stable/reference/exit-codes.html, https://docs.pytest.org/en/stable/how-to/usage.html, https://docs.python.org/3/library/shutil.html#shutil.copytree

### 4. Making a patch without git (confidence: high)
- `difflib.unified_diff(a_lines, b_lines, fromfile, tofile, lineterm="")`. For a **new file**, `a_lines=[]`, `fromfile="/dev/null"`, `tofile="b/tests/test_x.py"`. difflib emits `--- /dev/null`, `+++ b/...`, and `@@ -0,0 +1,N @@`. This is the format `patch -p1` and `git apply` accept for file creation.
- Pitfalls: read with `splitlines(keepends=True)` and use the default `lineterm`, *or* strip the newlines and join with `"\n"`. Mixing the two produces double or missing newlines. If the file doesn't end with a newline, add one; otherwise you'd need the `\ No newline at end of file` marker, which difflib doesn't emit. Validate with `patch --dry-run -p1 -d <fresh copy> < changes.patch` (`patch` ships with macOS).
- Sources: https://docs.python.org/3/library/difflib.html#difflib.unified_diff, https://www.gnu.org/software/diffutils/manual/html_node/Detailed-Unified.html

### 5. uv + pytest + coverage on Python 3.15.2 (confidence: high, verified locally)
- `uv venv -p 3.15` followed by `uv pip install pytest coverage` resolved and installed in about 0.25 s: **pytest 9.2.0, coverage 7.17.5**, pluggy 1.7.0, iniconfig 2.3.1, packaging 26.4, pygments 2.21.0. A real run with `--branch` plus a JSON report worked on 3.15.2.
- For the project, use `uv venv` plus `uv pip install` (or a `pyproject.toml` with `uv sync`). Tools: uv 0.13.8, `/opt/homebrew/bin/python3.15`.
- Sources: local run, https://docs.astral.sh/uv/pip/environments/, https://pypi.org/project/pytest/, https://pypi.org/project/coverage/

### 6. Risks (see the section below). Confidence: medium, based on one smoke call and general experience.

## Options identified
- **Model output contract:** (a) plain text, where the model returns one fenced ```python block that we extract. This is simple and robust, and the stub provider mirrors it easily. (b) `--json-schema` structured output (for example `{"test_code": str}`). Validated, but not tested locally, and it may add turns or latency.
- **Gap granularity:** (a) one model call per function that has missing lines (from `functions` in the coverage JSON). This gives small prompts and tidy quarantine. (b) one call per file. Fewer calls against the cap of 30, but larger prompts, and a single failure taints more tests.
- **Default model alias:** `sonnet` (fast and cheap, about $0.01 per trivial call) or `opus` (stronger, slower, and more expensive). This is a CLI flag either way.
- **Isolating the CLI from project config:** (a) `cwd` set to the run folder, (b) `--safe-mode`, or (c) both.
- **Install method:** `uv pip install` into `.venv` or a `pyproject.toml` with `uv sync`.

## Risks & pitfalls found
- **Latency and cost:** a trivial call took about 1.3 to 2.2 s and $0.012. Real test-writing calls with source context will probably take about 5 to 30 s each. In the worst case, 30 calls is several minutes, which matters for a recorded demo. Set the per-call timeout (for example 120 s) and run a small sandbox. Keep the stub provider ready for the demo fallback.
- **Nondeterminism:** generated tests differ between runs. Unit tests and evals must use the stub provider. Real runs should save each prompt and response in the run folder so they can be audited and resumed.
- **The model may "fix" the test to match a bug:** on the seeded bug, the fix loop might rewrite the assertion to accept the wrong value, so the test passes and hides the bug. Mitigation: a fix prompt that says "the target is correct per its docstring/spec; do not weaken assertions to match observed output", plus clear docstrings in the sandbox. This can't be fully prevented. Flag it in the README.
- **Prompt leakage:** if the CLI runs with cwd inside the project, it loads the project's CLAUDE.md, agents, and skills. Run it from the run folder and/or use `--safe-mode`.
- **`--bare` breaks auth** on this machine (it needs `ANTHROPIC_API_KEY`).
- **`--max-turns` is undocumented in local help**, but it appears in the online reference. If it ever errors, drop it, since `--tools ""` already limits each call to one turn.
- **Keeping the target untouched:** only ever write inside `runs/<run-id>/`. Hash the target files (sha256) before and after a run and assert they match, which also proves the "byte-for-byte unchanged" done-criterion. Watch for `__pycache__` / `.pytest_cache` / `.coverage` getting written *into the target*: always run with `cwd` set to the copy, with `PYTHONDONTWRITEBYTECODE=1` and `-p no:cacheprovider`.
- **Coverage path keys:** keys in `files` are relative to cwd (for example `pkg/__init__.py`). Run coverage from the copy root so the paths match the original's relative paths for display and patch paths.
- **Python 3.15:** no problems seen with pytest 9.2 or coverage 7.17.5. Generated tests might use deprecated APIs, but that's a low risk.
- **Brief inconsistency (for the main session):** `docs/00-brief.md` still says "Status: DRAFT, awaiting user approval", while the guidance log records approval at 14:06. You may want to update the status line.

## Out of scope, flagged
- Branch-coverage-driven gap selection (the `missing_branches` data is available if it's ever wanted).
- Running generated tests in parallel (pytest-xdist) to save time.

## Sources
- Local: `claude --help` (2.1.331), one smoke call, and a scratchpad uv venv run (pytest 9.2.0, coverage 7.17.5, Python 3.15.2)
- https://code.claude.com/docs/en/cli-reference
- https://code.claude.com/docs/en/headless
- https://code.claude.com/docs/en/agent-sdk/structured-outputs
- https://docs.python.org/3/library/subprocess.html#subprocess.run
- https://coverage.readthedocs.io/en/latest/cmd.html#json-reporting-coverage-json
- https://coverage.readthedocs.io/en/latest/changes.html
- https://docs.python.org/3/library/ast.html
- https://docs.pytest.org/en/stable/reference/exit-codes.html
- https://docs.pytest.org/en/stable/how-to/usage.html
- https://docs.python.org/3/library/shutil.html#shutil.copytree
- https://docs.python.org/3/library/difflib.html#difflib.unified_diff
- https://www.gnu.org/software/diffutils/manual/html_node/Detailed-Unified.html
- https://docs.astral.sh/uv/pip/environments/
- https://pypi.org/project/pytest/
- https://pypi.org/project/coverage/
