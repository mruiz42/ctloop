# 05 — Validation

Validator run: 2026-11-24, 14:38 to 14:41 PST. Every result below comes from a real run in `/Users/michael/code/interview`. I did not edit any source code or anything under `targets/`.

## Open questions
None.

## Verdict
**PASS WITH ISSUES.** Every definition-of-done item passes. There is one doc-only issue that doesn't block the demo (I1 below).

## Automated checks

| # | Done criterion / check | Command | Expected | Actual | Result |
|---|---|---|---|---|---|
| 1 | Unit tests green (stub) | `uv run pytest -q` | 50 passed | `50 passed in 9.32s` | PASS |
| 2a | One command raises coverage | `uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses` | coverage up, exit 0 | run `20261124-143850-98c423`: `coverage 28.3% -> 92.5%  tests {'quarantined': 1, 'passed': 4}  calls 7/30  stop=completed  exit=0` | PASS |
| 2b | Every patched test passes | the pr.md of that run, plus applying the patch to a scratch copy and running pytest | PASS | pr.md says "Combined suite in verify/ (patch applied via patch -p1): PASS". On a scratch copy in the scratchpad: `13 passed` | PASS |
| 2c | Patch applies cleanly | `cp -R targets/sandbox <scratch>; cd <scratch>; patch -p1 --dry-run < runs/…98c423/pr/changes.patch` | exit 0 | patched 4 files (`test_gen_{dates_days_in_month,pricing_total_with_tax,strings_truncate,strings_is_palindrome}.py`), `PATCH_EXIT=0` | PASS |
| 3a | Failing test fixed | the same run | truncate passes after a fix | pr.md: `strings_truncate` with "fix attempts: 1", included in the patch | PASS |
| 3b | Failing test stops cleanly (quarantined) | the same run | is_leap_year quarantined and reported | pr.md, "Quarantined: suspected target bugs": `sandbox.dates.is_leap_year`, reason "no progress: same failure as the previous attempt", with the failure output (`is_leap_year(2000)` is False). Not in the patch. | PASS |
| 3c | Cap stops cleanly, exit 3 | `… --max-calls 4` | exit 3, PR folder written | `EXIT=3`, `calls 4/4 stop=call_cap`, coverage 28.3% -> 71.7%, `pr/changes.patch` and `pr/pr.md` present, and pr.md lists is_leap_year and truncate as "Skipped: call cap" | PASS |
| 3d | Resume completes | `uv run python -m ctloop run-all --resume 20261124-143903-1d529b --max-calls 30` | completes | `coverage 28.3% -> 92.5%  calls 7/30  stop=completed  exit=0` (call count carried over across the resume) | PASS |
| 4 | PR folder on every run | `ls runs/<id>/pr` for all 4 runs (completed, capped, resumed, real) | `changes.patch` + `pr.md` | present in all 4 | PASS |
| 5a | Target byte-for-byte unchanged | `shasum` over every file in `targets/sandbox`, before (14:38) and after (14:39) all runs | identical | `diff` is empty (`HASH_SAME`). pr.md also prints "Target integrity: PASS: 6 files, sha256 …" | PASS |
| 5b | git clean, no pycache | `git status --short targets`; `find targets -name __pycache__` | no output | no output for either | PASS |
| 6a | Core logic unit-tested | `uv run pytest -q` | covers guard, prompts, state, sandbox, budget, stages | 50 tests across `tests/test_sandbox.py`, `test_state_workspace.py`, `test_providers.py`, `test_prompts_guard.py`, … | PASS |
| 6b | README exists and its commands work | ran the README demo commands verbatim (rows 2a, 3c, 3d) | they work | all worked as documented. The stub result matches the README ("28.3% -> 92.5% in 7 calls") | PASS |
| 7a | sandbox-exec active, no profile file | `ls runs/<new runs>/sandbox.sb` | absent | absent for all 4 runs from this validation. Only 3 older runs from before the fix (14:30-14:31) still have one. pr.md: "Test sandbox: sandbox-exec (no network, writes only in the run folder)" | PASS |
| 7b | Sandbox tests (blocked writes and network, fail-closed) | `uv run pytest -q tests -k "sandbox or network or write or cap or budget"` | pass | `17 passed`. Includes `test_write_outside_run_blocked`, `test_network_blocked`, `test_tool_state_not_writable`, `test_no_profile_file_to_tamper_with`, `test_secret_reads_denied`, `test_missing_sandbox_exec_fails_closed`, plus the `safe_path` dotdot and symlink escape tests | PASS |
| 7c | Caps enforced | rows 3c and 8, plus `test_budget_stops_at_call_cap` and `test_budget_stops_at_spend_cap` | stops at the cap | the call cap stopped at exactly 4/4 and 5/5, and the spend cap is unit-tested | PASS |
| 8 | ONE real provider run (user-approved) | `uv run python -m ctloop run-all --target targets/sandbox --model sonnet --max-calls 5` | clean finish, small spend | run `20261124-143850-b22389`: `coverage 28.3% -> 58.5%  tests {'skipped': 2, 'passed': 3}  calls 5/5  spend $0.0346  stop=call_cap  exit=3`. `claude --safe-mode: on`, and the combined verify suite PASSES | PASS |

That was the only real model call I made. Every other run used the stub.

## Failures
None that block. One minor issue:

- **I1 (docs, low):** `README.md:59` still lists `sandbox.sb` in the run-folder layout. Since the B1 fix, the profile is passed inline and no `sandbox.sb` file gets written. Fix: remove `sandbox.sb` from that line. This goes back to the implementer, only if the user approves.
- **Note (not a defect):** the real-run line in the README uses `--max-calls 10`. I ran `--max-calls 5` as approved, so the 10-call version hasn't been run by me.

## Manual validation needed: demo recording guide (about 5 minutes)
Run every command from `/Users/michael/code/interview`.

1. `uv run pytest -q`. **Pass:** `50 passed`. **Fail:** any `failed` or `error`.
2. `uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses`. **Pass:** the last lines show `coverage 28.3% -> 92.5%`, `tests {'quarantined': 1, 'passed': 4}`, `calls 7/30`, `exit=0`, and a `RUN_ID`. Copy that ID.
3. `cat runs/<RUN_ID>/pr/pr.md`. Point out the before/after coverage table, "Combined suite … PASS", `truncate` with "fix attempts: 1", the **Quarantined: suspected target bugs** section for `is_leap_year` (2000 should be a leap year), and "Target integrity: PASS".
4. `cat runs/<RUN_ID>/pr/changes.patch`. **Pass:** only new `tests/test_gen_*.py` files (4 of them), with no target source changes and no is_leap_year test.
5. Cap path: `uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses --max-calls 4; echo $?`. **Pass:** `calls 4/4 stop=call_cap exit=3`, then `3`. `ls runs/<NEW_ID>/pr` shows both files.
6. Resume: `uv run python -m ctloop run-all --resume <NEW_ID> --max-calls 30`. **Pass:** `28.3% -> 92.5%`, `calls 7/30`, `stop=completed`.
7. Target untouched: `git status --short targets`. **Pass:** no output.
8. (Optional, real model, costs about $0.03-0.07) `uv run python -m ctloop run-all --target targets/sandbox --model sonnet --max-calls 5`. **Pass:** it ends at `stop=call_cap exit=3` with coverage above 28.3% (I got 58.5%) and a small spend. **Fail:** a traceback, or `claude` not logged in. If it fails, skip it: the stub demo is the planned fallback. You can also show the earlier real run, `runs/20261124-143850-b22389/pr/pr.md`.

## Not tested
- The spend cap ($2 per run) on a real run. That would mean spending real money, so it's covered only by the unit test `test_budget_stops_at_spend_cap`.
- The `--safe-mode` fallback path (cwd only, with a warning) on a real run. `--safe-mode` worked, so the fallback never triggered.
- The per-call timeout on a real hung call. I can't trigger that cheaply or deterministically.
- A fresh clone plus `uv sync`. The `.venv` already existed, and with the demo 10 minutes out I didn't rebuild the environment from scratch.
