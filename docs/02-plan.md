# 02 — Plan: Converging Test Loop

Written 2026-11-24 at about 14:10 to 14:13. Inputs: `docs/00-brief.md` (approved 14:06, including its "Decisions added after the investigation"), `docs/01-investigation.md` (approved 14:08), and `docs/guidance-log.md`.
Sizing: the implementer has about 20 to 25 minutes. The plan is minimal on purpose. Each item below either serves a done-criterion or is a production guard that costs a few lines.

## Open questions
None of these block the plan. Each has a recommendation, and approving the plan as written means accepting them.

1. **The four choices the user asked to have decided here** (details under Decisions D1 to D4). Recommended: **D1** plain text with one fenced block, **D2** one model call per function, **D3** default model `sonnet`, **D4** `pyproject.toml` + `uv sync` (non-packaged project, `uv.lock` committed to the folder). Confirm or override each one.
2. **Evolve-stage artifacts (`feedback/submissions.jsonl`, `evals/`).** Options: (a) defer both, (b) add only a one-line append to `feedback/submissions.jsonl` per `run-all` (about 10 lines, no evals), (c) build both now. **Recommendation: (a) defer.** They aren't in the brief or the definition of done, Evolve is "started by the dev only", and the time is tight. The PR folder and `state.json` already hold everything a future submissions record needs.
3. **Exit code when the call cap or spend cap is hit** (D8). Recommended: exit `3` with the PR folder written. The alternative is exit `0` and printing a message. Confirm.

Already decided by the user (in the brief, applied here): `--max-budget-usd 0.25` per call plus a `--max-run-usd 2.00` per-run cap tracked from `total_cost_usd` with a clean stop (D13); the demo runs on the stub, plus one real run if time allows (D11); isolation is cwd = run folder **and** `--safe-mode`, with a runtime fallback to cwd only plus a warning (D5); the validator may do one real run at about a 5-call cap.

## Goal recap
We're building, as if it would ship to production, one command that points at a Python package folder and works on a **copy** of it. It measures the coverage baseline, finds the functions that have untested lines, asks a model (`claude -p`, or a deterministic stub) to write tests for them, runs each test on its own, and fixes failing *tests* (never the target) up to a cap. Tests that still fail are quarantined as suspected target bugs. Every run ends with a PR folder holding `changes.patch` and `pr.md` (coverage before and after, plus the quarantined tests with their failure output). The run is capped (30 calls, 3 fix attempts per test, $0.25 per call and $2 per run by default, plus timeouts; all set by flags) and stops cleanly at the cap. It can resume from `state.json`, and it proves with hashes that the target is byte-for-byte unchanged. The demo is due at 14:50 and must be deterministic, so the stub provider drives it, plus one real `claude -p` run if time allows.

## Architecture

An engine package `ctloop/` (library) with a thin argparse CLI on top. The stages are pure functions of `(RunContext) -> None` that read and update the persisted `RunState`.

```
                 ctloop CLI (argparse)  -- gaps | write | run | fix | pr | run-all [--resume ID]
                          |
                          v
   +---------------- stages.py (orchestration, one function per stage) ----------------+
   |  gaps  --> write --> run --> fix --> pr                                           |
   +---|---------|--------|-------|--------|-------------------------------------------+
       |         |        |       |        |
       |   prompts.py  executor.py  guard.py  report.py (changes.patch + pr.md)
       |   (build +     (pytest /     (anti-weakening    |
       |    extract)    coverage      AST checks)         |
       |         |      subprocess)                       |
       v         v        ^                               v
   gaps.py   providers.py |                        workspace.py
   (coverage  Provider    |                        (copytree into runs/<id>/work,
    JSON ->   protocol:   |                         sha256 tree hash + verify,
    Gap list) Claude|Stub |                         verify copy for the patch)
              + Budgeted  |
              wrapper     |
                          |
   state.py: RunState <-> runs/<id>/state.json (atomic write after every change)

Filesystem (the ONLY place the tool writes):
runs/<id>/state.json   run.log   calls/NNN-<kind>-<gap>.{prompt,response}.txt
         work/      (copy of target + generated tests, where tests are run)
         verify/    (fresh copy + changes.patch applied, where "after" coverage is measured)
         quarantine/<test files>
         pr/changes.patch   pr/pr.md
```

Data flow per run:
1. **gaps**: hash the target, copy it to `work/`, run the baseline coverage (existing tests only) and save it, then turn coverage JSON `files[*].functions` into a list of `Gap` objects (module path, qualname, start line, missing lines). Skip the `""` module-level key and functions with no missing lines. Order is deterministic (file, then line).
2. **write**: for each pending gap, if the budget allows: build the prompt, call the provider, extract the code, run the guard (it must import the target module and contain at least one assert), and write `work/tests/test_gen_<module>_<func>.py`. Status becomes `written`.
3. **run**: run each `written` test file alone with pytest (with a timeout). Status becomes `passed` or `failed`, and a tail of the output is stored.
4. **fix**: for each `failed` test, while `attempts < max_fix` and the budget allows: build the fix prompt (function source, docstring, current test, failure tail), call the provider, extract, guard against the previous version, write, and re-run. Results: `passed`, quarantined at the cap, or quarantined early on a `VERDICT: TARGET_BUG` reply (D7).
5. **pr**: build `changes.patch` (new files only, `passed` tests only). Apply it to a fresh `verify/` copy and run the full suite with coverage (that's "after"). Write `pr.md`, then verify the target hash. Record `stop_reason`.

Every command ends by re-checking the target hash. A mismatch raises `TargetModifiedError` and exits 1.

## Decisions

### Decision D1: model output contract (plain text vs `--json-schema`)
- Option A: plain text. The system prompt requires exactly one fenced ```python block, and we extract the first one (or the last, if there are several; see the implementation notes). Pros: verified locally, simplest code, the stub mirrors it trivially, and the extractor is easy to unit-test. / Cons: the model can add prose or several blocks; parsing is heuristic.
- Option B: `--json-schema '{"test_code": string}'`. Pros: validated structure. / Cons: untested here, the shape of the output field is only medium confidence, it may add latency or turns, and there's more failure surface in a 20-minute build.
- Recommendation: **A**. The brief's time budget favors the verified path, and an extraction failure is handled like any other failed attempt (it counts against the cap and is reported). B can be added later as a provider option.
- Needs user call? Yes, the user asked for this one to be decided in the plan. Confirm at approval.

### Decision D2: granularity (one model call per function vs per file)
- Option A: per function (one gap = one function with missing lines = one test file). Pros: small focused prompts, quarantine and patch stay per function (one bad test doesn't drop the others), clean resume. / Cons: more calls. The sandbox has about 6 gap functions, so about 6 write calls plus a few fixes, which is well under 30.
- Option B: per file. Pros: fewer calls. / Cons: one failure taints a whole file's tests, the fix prompts get bigger, and quarantine becomes coarse.
- Recommendation: **A**. Per-function quarantine is what makes "every test in the patch passing" and "quarantined and reported" clean. The cap protects against large targets, and the leftover gaps are reported as `skipped (call cap)`.
- Needs user call? Yes (asked for). Confirm at approval.

### Decision D3: default model (`sonnet` vs `opus`)
- Option A: `sonnet`. Pros: fast (the demo is being recorded) and cheap, and unit tests for small functions are well within its range. / Cons: a somewhat higher chance of weakening an assertion or producing a test that needs a fix.
- Option B: `opus`. Pros: stronger reasoning about docstrings versus behavior. / Cons: slower and more expensive, so 30 calls could take minutes.
- Recommendation: **A, `sonnet` by default**, with `--model opus` available. The demo runs on the stub anyway, and a real run should be fast.
- Needs user call? Yes (asked for). Confirm at approval.

### Decision D4: install method (`uv pip install` vs `pyproject.toml` + `uv sync`)
- Option A: `uv venv` + `uv pip install pytest coverage`. Pros: verified, about 0.25 s, nothing to write. / Cons: dependencies aren't declared or locked, so the setup isn't reproducible from the repo alone.
- Option B: `pyproject.toml` (with `requires-python = ">=3.15"`, deps `pytest`, `coverage`, `[tool.uv] package = false`, and pytest `testpaths = ["tests"]`) + `uv sync` + `uv.lock`. Pros: declared and locked deps, `uv run` works, and it also holds the pytest config the engine's own tests need. / Cons: about 1 minute more. `package = false` means no console script, so you run `uv run python -m ctloop`.
- Recommendation: **B with `package = false`**. Reproducible setup is a production expectation, we need `pyproject.toml` for `testpaths` anyway (so the repo-root pytest doesn't collect the sandbox's or the runs' tests), and skipping the build backend avoids any packaging risk on 3.15. If `uv sync` misbehaves, fall back to A, which is verified.
- Needs user call? Yes (asked for). Confirm at approval.

### Decision D5: isolating `claude -p` from the project's config (DECIDED by the user)
- The user's decision: cwd = `runs/<id>/` **and** `--safe-mode`. If `--safe-mode` errors, fall back to cwd only and log a warning.
- How it's built: argv is `claude -p --output-format json --tools "" --no-session-persistence --model M --max-budget-usd X --safe-mode [--max-turns 1]`, with the prompt on stdin, never `shell=True`, and `subprocess.run(timeout=call_timeout)`. `--bare` is never used.
- Fallback detection: the call exits non-zero, stdout isn't valid JSON, and stderr mentions the option (for example "unknown option" plus "safe-mode"). In that case the provider logs a WARNING once, disables `--safe-mode` for the rest of the run (saved in state as `safe_mode_supported: false`), and retries that same call once without it. The failed attempt happened at argument parsing, before any model work, so it **doesn't count** against the call or spend caps, but it is logged. `--max-turns 1` gets the same treatment. A unit test covers this with a monkeypatched `subprocess.run`.
- Needs user call? No. Already decided.

### Decision D6: anti-weakening guard (prompt rules + cheap AST check)
- Option A: prompt rules only. Pros: zero code. / Cons: nothing enforces them.
- Option B: prompt rules plus an AST check in `guard.py`, applied to every candidate test. (1) It must parse. (2) It must import the target module (`<package>.<module>`), so it can't redefine the function locally. (3) It must have at least one assertion, meaning `ast.Assert` nodes or `pytest.raises` contexts. (4) It must not use `pytest.skip`, `skip` or `xfail` marks, `assert True`, or bare `except` that swallows failures. (5) **For fixes:** its assertion count must be at least the previous version's. A rejected candidate counts as a used attempt, and the reason is logged in the history and `pr.md`. Pros: catches the cheap ways to cheat for about 40 lines, and it's unit-testable with no model. / Cons: it can't catch a changed expected value (`== False` instead of `== True`).
- Prompt rules (write and fix): "The function's docstring is the specification. Assert the documented behavior. Never change an expected value to match observed output. If the failure shows the function contradicts its docstring, reply with `VERDICT: TARGET_BUG` and a one-line reason instead of code."
- Recommendation: **B**. It's cheap and directly protects the brief's "never hide the seeded bug" intent. Accepted residual risk: a changed expected value. It's mitigated by the TARGET_BUG verdict, the clear docstrings, and `pr.md` being a human-reviewed artifact. The README says so.
- Needs user call? No (the user asked for "prompt rules, and optionally a cheap check"). This is the cheap check.

### Decision D7: early quarantine on a `VERDICT: TARGET_BUG` reply
- Option A: always spend all `max_fix` attempts before quarantining. Pros: simpler. / Cons: wastes up to 3 calls per real bug, which hurts the 30-call cap and the demo's runtime.
- Option B: if a fix reply contains `VERDICT: TARGET_BUG`, quarantine right away with the model's reason. Pros: saves calls and gives a better report. / Cons: the model could give up too early. That's acceptable, because quarantined tests still appear in `pr.md` for a human.
- Recommendation: **B** (about 5 lines). The stub demo uses the *exhaust-the-cap* path for the seeded bug, so the brief's "stops cleanly at cap" is what gets shown. A unit test covers the verdict path.
- Needs user call? No (low stakes). Flagged.

### Decision D8: how a run ends at the call cap or spend cap, and exit codes
- Option A: both budgets (calls and dollars, see D13) are checked *before* each call. Gaps that can't be served are marked `skipped` with the reason "call cap" or "spend cap". The pipeline still runs `run` (no calls) and `pr`. `stop_reason` is `"call_cap"` or `"spend_cap"`, and the exit code is **3**. `BudgetedProvider` also raises `BudgetExhausted` as a hard safety net. Exit codes: 0 = completed (quarantines included), 1 = error (including target modified or a provider/CLI setup error), 2 = usage (argparse), 3 = stopped at a cap with the PR written.
- Option B: the same flow, but exit 0 with a printed warning. Pros: simpler for scripts. / Cons: a caller (or CI later) can't tell partial from complete.
- Recommendation: **A**. It's a clean stop that's still machine-readable, and the PR folder is always written.
- Needs user call? Minor. It's open question 3.

### Decision D9: resume semantics and cap overrides
- Option A: caps and provider settings are stored in `state.json` when the run is created. On resume, any flag that's given explicitly overrides the stored value and is saved again. Stages are idempotent and skip work that's already done, by stage and by test status. `calls_used` persists, so the cap is **per run**, across resumes. Pros: "hit the cap, then `run-all --resume ID --max-calls 40`" works, which is useful for the demo. / Cons: a small amount of merge logic.
- Option B: settings are frozen at creation. Pros: simpler. / Cons: you can't continue a capped run.
- Recommendation: **A**.
- Needs user call? No (it follows the brief's "resumable").

### Decision D10: where "after" coverage is measured
- Option A: in `work/`, after moving the quarantined files out. Pros: simple. / Cons: it doesn't prove the *patch* gives that result.
- Option B: in a fresh `verify/` copy of the target with `changes.patch` applied by `patch -p1` (ships with macOS). Pros: it proves the patch applies and that every test in it passes together, which is exactly the done-criterion. / Cons: depends on the `patch` binary, and costs about 15 lines.
- Recommendation: **B**. If `patch` isn't found, fall back to copying the passed files and note it in `pr.md`. If the combined suite fails (for example, tests interfering with each other), `pr.md` says so plainly, and the command exits 1.
- Needs user call? No.

### Decision D11: what the stub provider is and how the demo uses it
- Option A: a `StubProvider` built from a mapping `key -> response text`, where the key is `<gap_slug>.write` or `<gap_slug>.fix<N>`. Each prompt carries a machine-readable header line, `CTLOOP-KEY: <gap_slug>.<kind>`, that the stub reads. If there's no `fixN` key, the stub falls back to the latest available response for that gap, so returning the same test means "fails again". A missing `write` key raises `ProviderError`. The CLI loads the mapping from `--stub-dir demo/stub_responses/` (files named `<key>.txt` that contain model-style text with a fenced block, so the extractor is exercised too). Unit tests build the stub straight from a dict. Pros: deterministic, auditable, and it covers all paths. / Cons: the demo responses are tied to the sandbox, which is fine because they live in `demo/` and not in the engine.
- Option B: a generic stub that synthesizes tests. Pros: none worth it. / Cons: it can't meaningfully raise coverage.
- Recommendation: **A**.
- The user's decision on the demo: the stub drives it, plus one real `claude -p` run if time allows. The validator may also do one small real run (`--max-calls 5`). Everything else, including every unit test, uses the stub.
- Needs user call? No (the brief requires a deterministic stub).

### Decision D12: execution model and safety of running generated code
- Generated tests are arbitrary model-written code, run on the user's machine. Options: (A) run in the copy with timeouts, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and `cwd=` the copy; (B) also add an OS sandbox or container.
- Recommendation: **A**, sequential and one test file per subprocess. B is out of scope for 60 minutes. The risk is accepted and documented in the README: the model has no tools (`--tools ""`), its output is only Python test code, and that code runs with the user's permissions inside `runs/<id>/work/`.
- Needs user call? No (accepted risk, listed under Risks).

### Decision D13: spend caps (DECIDED by the user)
- The user's decision: `--max-budget-usd 0.25` is passed to every `claude -p` call (the per-call cap), and `--max-run-usd 2.00` is a per-run cap. Both are CLI flags, stored in state, and can be overridden on resume (D9).
- How it's built: `ClaudeCLIProvider` returns the response text plus `total_cost_usd` from the JSON. `BudgetedProvider` adds it to `state.cost_usd` (persisted). Before each call it stops if `cost_usd >= max_run_usd`. Because the cost of a call is only known afterwards, a run can go over by at most one call's cost, which is itself capped at about $0.25 (the CLI's estimate "can pass" slightly). This is documented. A call that times out has an unknown cost: it's counted as $0 and logged as a WARNING, because the CLI kills the child before reporting. The stub reports a cost of $0 (with an optional configurable per-call cost so unit tests can exercise the spend cap). pr.md shows the spend used and the cap.
- Accepted risk: an overshoot of up to one call's cost, and unmeasured cost on a timed-out call.
- Needs user call? No. Already decided.

## File / module layout

```
/Users/michael/code/interview/
  pyproject.toml            # deps: pytest, coverage; [tool.uv] package=false; [tool.pytest.ini_options] testpaths=["tests"]
  uv.lock
  README.md
  .gitignore                # + .venv/, runs/, __pycache__/
  ctloop/
    __init__.py             # version, public exceptions
    __main__.py             # python -m ctloop -> cli.main()
    cli.py                  # argparse: subcommands + shared flags; logging setup; exit codes
    config.py               # RunConfig dataclass (caps, timeouts, model, provider, stub_dir, max_budget_usd, max_run_usd), defaults, merge-on-resume
    state.py                # RunState, TestRecord, Status enum; load/save (atomic tmp + os.replace); new_run_id()
    workspace.py            # copy_target(), tree_hash(), verify_untouched() -> TargetModifiedError, make_verify_copy()
    executor.py             # run_pytest_file(), run_coverage() -> CoverageResult (percent, per-file); subprocess + timeout + env
    gaps.py                 # parse coverage JSON -> list[Gap]; function_source() via ast
    prompts.py              # SYSTEM_PROMPT, build_write_prompt(), build_fix_prompt(), extract_code(), parse_verdict()
    guard.py                # check_candidate(code, module, previous=None) -> GuardResult(ok, reasons)
    providers.py            # Provider Protocol -> Completion(text, cost_usd); ClaudeCLIProvider (+ safe-mode fallback); StubProvider; BudgetedProvider (calls + $); ProviderError, BudgetExhausted
    stages.py               # RunContext; stage_gaps/write/run/fix/pr; run_all(); call logging to runs/<id>/calls/
    report.py               # build_patch() (difflib, new files), write_pr_md()
  demo/
    stub_responses/         # <gap_slug>.write.txt / .fixN.txt for the sandbox
  targets/
    sandbox/                # the TARGET (never modified)
      sandbox/__init__.py
      sandbox/strings.py    # slugify (tested), truncate, is_palindrome
      sandbox/dates.py      # is_leap_year (SEEDED BUG), days_in_month
      sandbox/pricing.py    # apply_discount (tested), total_with_tax
      tests/test_strings.py # partial: slugify only
      tests/test_pricing.py # partial: apply_discount only
  tests/                    # engine unit tests (stub only, no network)
    conftest.py             # fixtures: tmp runs dir, sandbox path, stub from dict
    test_gaps.py
    test_prompts_guard.py
    test_providers.py
    test_state.py
    test_report.py
    test_pipeline_stub.py   # end-to-end on a temp copy of the sandbox
  runs/                     # gitignored, created at runtime
```

**The sandbox's seeded bug:** `dates.is_leap_year(year)`. The docstring states the full Gregorian rule ("divisible by 4, except centuries, unless divisible by 400", with doctest-style examples `is_leap_year(2000) -> True` and `is_leap_year(1900) -> False`). The code leaves out the 400 rule, so 2000 returns `False`. `days_in_month` calls it. Its demo tests use 2024 and 2023 (not 2000), so they pass and only the `is_leap_year` test is quarantined.

**The demo stub responses** cover three paths:
- `strings.is_palindrome`, `pricing.total_with_tax`, and `dates.days_in_month`: the `write` response passes on the first try.
- `strings.truncate`: the `write` response has a test bug (a wrong expected ellipsis, say `"..."` against the documented `"…"`, or a wrong length). `fix1` corrects it. This is the **converges** path.
- `dates.is_leap_year`: the `write` response asserts the documented behavior (2000 is a leap year), so it fails. There are no fix files, so the stub returns the same test each time, it fails 3 times, and it's **quarantined at the cap**. pr.md lists it as a suspected target bug with the failure output.

Expected stub demo totals: 5 write calls + 1 fix call + 3 fix calls = 9 model calls. Coverage goes from about 35-45% up to 90% or more. The stub's cost is $0, so the spend cap isn't hit in the demo; a unit test covers it. With `--max-calls 4`, the demo shows the cap path (exit 3, with the remaining gaps `skipped`). Then `--resume ID --max-calls 30` finishes the run.

## Implementation steps
Each slice ends at a **checkpoint** with a **local git commit** (one small commit per green slice; no remote, no push, and the tool itself never uses git; see A5). The implementer reports "slice N green" with the test output and the commit hash. The demo path comes first, and the real Claude provider and README come last, but both are required.

**Slice 1: scaffold + sandbox target (about 3 min)**
- `pyproject.toml`, `uv sync`, `.gitignore`, the `ctloop/` skeleton, and the `targets/sandbox` package with its partial tests and the seeded bug.
- Verify: `uv run python -c "import ctloop"`; run the sandbox tests **on a scratch copy** (never in place, so no `__pycache__` lands in the target), and they pass. `uv run pytest` collects only `tests/`.

**Slice 2: workspace + executor + gaps + state + the `gaps` command (about 5 min)**
- `tree_hash` (sha256 over sorted relative paths and their bytes, skipping `__pycache__`, `.pytest_cache`, `.coverage*`), `copy_target`, `run_coverage`, `run_pytest_file`, `gaps.parse`, `RunState` save/load, and `ctloop gaps --target targets/sandbox [--package sandbox]`, which prints the run id and the gap table.
- Tests: `test_gaps.py` (a fixture coverage JSON gives the expected gaps, skips `""` and fully covered functions), `test_state.py` (round trip, atomic save), and a hash test (edit a byte and the hash changes; `verify_untouched` raises).
- Checkpoint: the `gaps` command on the sandbox shows the baseline percentage and about 6 gaps, and the target hash is unchanged.

**Slice 3: providers + prompts + the `write` stage with the stub (about 4 min)**
- `Provider.complete(prompt: str, *, system: str) -> Completion(text, cost_usd)`; `StubProvider`; `BudgetedProvider` (counts calls and dollars in state and raises `BudgetExhausted` at either cap); `ClaudeCLIProvider` (argv list, stdin, timeout, `--max-budget-usd`, `--safe-mode` with the D5 fallback, JSON parse, `is_error`/`subtype` checks, `total_cost_usd`, `shutil.which` check, raises `ProviderError` with a clear message); `extract_code`; `build_write_prompt` with the `CTLOOP-KEY` header; `guard.check_candidate` (the write-time checks); and the `write` stage. Every prompt and response is saved under `calls/`.
- Tests: `test_providers.py` (stub keys and fallback; the budget stops at N calls and at the dollar cap; Claude provider with `subprocess.run` monkeypatched for success with cost, `is_error`, a non-JSON reply, a timeout, a missing binary, and the safe-mode fallback: retried once without the flag, a warning logged, the call not counted); the extractor in `test_prompts_guard.py` (one block, several blocks, no block).
- Checkpoint: `ctloop write --run ID --provider stub --stub-dir demo/stub_responses` writes 5 test files into `work/tests/`.

**Slice 4: `run` + `fix` + guard + quarantine (about 4 min)**
- The `run` stage, and the `fix` loop with `build_fix_prompt`, the weakening check, `parse_verdict` (TARGET_BUG), quarantine (the file moves to `quarantine/`, with the failure tail kept in state), and `skipped` on the cap.
- Tests: guard rejects fewer asserts, a missing import, skip/xfail, and `assert True`. Stub-driven fix loop cases: converges on fix1; quarantined after 3; quarantined early on a verdict; stops at the cap with the remaining tests `skipped`.
- Checkpoint: on the stub run, `truncate` is `passed` after 1 fix and `is_leap_year` is `quarantined` with 3 attempts.

**Slice 5: `pr` + `run-all` + resume + exit codes (about 5 min)**
- `build_patch` (difflib for new files, `--- /dev/null` / `+++ b/tests/...`, a trailing newline is guaranteed), the `verify/` copy with `patch -p1` applied (and the fallback), "after" coverage, `pr.md`, the final hash check, `run-all` with `--resume`, flag overrides that get saved, and exit codes 0/1/3.
- `pr.md` sections: summary (run id, provider/model, calls used/cap, spend used/cap in USD, safe-mode on or fell back, stop reason); coverage before -> after (total plus per file); tests added (file -> function, attempts); **quarantined: suspected target bugs** (function, attempts, reason/verdict, fenced failure tail of up to about 40 lines); skipped (call cap); guard rejections; target integrity (PASS/FAIL with the file count); how to apply (`patch -p1 -d <target> < changes.patch`).
- Tests: `test_report.py` (the patch for a new file has the right headers and applies with `patch --dry-run`; pr.md contains the sections); `test_pipeline_stub.py` (end to end on a **tmp copy** of the sandbox: after > before, every patched test passes in `verify/`, `is_leap_year` is quarantined, the target hash is unchanged, `--max-calls 4` gives exit 3 with a PR folder and resume finishes, and `state.json` is reloadable).
- Checkpoint: the demo commands below work end to end with the stub.

**Slice 6: real-provider smoke + README (about 3 min)**
- One real smoke call to confirm the argv works (it also exercises the D5 fallback live if `--safe-mode` or `--max-turns` errors), then, if time allows, the user-approved real `run-all --provider claude --max-calls 10` on the sandbox (watch the clock: about 6 or more calls of 5 to 30 s each, cost under $2 by cap).
- README: what it is and why; setup (`uv sync`); quickstart/demo; commands and stages (each one on its own); run folder layout; resume; caps and flags; providers (claude/stub, isolation flags); safety guarantees (copy-only, hash check, no target edits, generated code runs locally); the anti-weakening guard and its limits; exit codes; running the engine's tests; known limits.
- Checkpoint: `uv run pytest` is green, and the README commands have been copy-pasted and run.

**Cut list if behind schedule (in this order):** the real `run-all` in slice 6 → the per-file coverage table in pr.md (keep the totals) → the `patch` binary in D10 (copy the files instead and say so) → D7's verdict short-circuit. These stay no matter what: copy-only execution, the hash check, the caps, quarantine, resume, the PR folder, the stub unit tests, and the README.

### CLI surface
```
python -m ctloop gaps    --target PATH [--package NAME] [--runs-dir runs] [shared flags]   # creates a run, prints RUN_ID
python -m ctloop write   --run RUN_ID [shared flags]
python -m ctloop run     --run RUN_ID
python -m ctloop fix     --run RUN_ID [shared flags]
python -m ctloop pr      --run RUN_ID
python -m ctloop run-all (--target PATH [--package NAME] | --resume RUN_ID) [shared flags]
shared flags: --provider {claude,stub} (default claude) --stub-dir DIR --model sonnet
              --max-calls 30 --max-fix-attempts 3 --call-timeout 120 --test-timeout 60
              --max-budget-usd 0.25 (per call) --max-run-usd 2.00 (per run) --log-level INFO
```
Validation: the target must exist, contain the package directory with an `__init__.py`, and must not be inside `--runs-dir`. The call and attempt caps must be positive integers, and the dollar caps positive numbers. `--provider stub` requires `--stub-dir`. A stage run out of order (for example `fix` before `write`) fails with a clear message. Logs go to stderr and to `runs/<id>/run.log`.

### Demo commands
```
uv sync
uv run pytest                                                     # engine unit tests (stub only)
uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses
cat runs/<RUN_ID>/pr/pr.md ; cat runs/<RUN_ID>/pr/changes.patch
uv run python -m ctloop run-all --target targets/sandbox --provider stub --stub-dir demo/stub_responses --max-calls 4   # exit 3
uv run python -m ctloop run-all --resume <RUN_ID> --max-calls 30                                                       # finishes
uv run python -m ctloop run-all --target targets/sandbox --model sonnet --max-calls 10                                 # real claude -p (if time allows)
```

## Test strategy
- **Unit tests (stub only, no network, no real `claude`):** gaps parsing (fixture JSON), state round trip, tree hash and verify, code extraction, verdict parsing, every guard rule, stub key/fallback, budget enforcement, the Claude provider against a monkeypatched `subprocess.run` (success, error, bad JSON, timeout, missing binary), patch format, and pr.md sections.
- **Integration (real pytest/coverage subprocesses, stub provider):** `test_pipeline_stub.py` runs the full pipeline on a `tmp_path` copy of the sandbox with `tmp_path` runs, then asserts: coverage goes up; every test in the patch passes in `verify/`; quarantine and its report; the cap path gives exit 3 and a PR folder; resume completes; the target hash is unchanged. This one test covers most of the definition of done. Expected run time is under 10 s.
- **Manual / validator:** per the user's decision, the validator may do **one** small real run (`run-all --provider claude --max-calls 5`); everything else uses the stub; `patch -p1 --dry-run` of `changes.patch` against the real target; check that nothing under `targets/sandbox` has changed (the hash, plus `find targets/sandbox -name __pycache__` comes back empty).
- No tests call the real model. The guard's limits (a changed expected value) are documented, not tested.

## Risks & mitigations
| Risk | Mitigation | Accepted? |
|---|---|---|
| **Time overrun** (20 to 25 min for 6 slices) | The demo path comes first, there's an explicit cut list, and the stub-driven integration test covers most of the done-criteria in one place | Partly. Nice-to-haves get cut first |
| The model weakens assertions to hide the seeded bug | The docstring is the spec in the prompt rules; the AST guard (assertion count, no skip/xfail, must import the target); the TARGET_BUG verdict; human review of pr.md | **A changed expected value isn't caught.** Accepted and documented |
| Generated code runs on the host | Copy only, `cwd` set to the copy, timeouts, no model tools, everything confined to `runs/<id>/` | **No OS sandbox.** Accepted for this scope and in the README |
| Target modified (bytecode, caches, a stray write) | Never run inside the target; `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`; hash at start and at the end of every command; `TargetModifiedError` means exit 1 | No |
| `claude -p` latency, cost, or nondeterminism | Default `sonnet`, per-call timeout, `--max-budget-usd 0.25` per call, `--max-run-usd 2.00` per run, the 30-call cap, every prompt and response saved; the demo runs on the stub | Real runs vary. A spend overshoot of up to one call is accepted (D13) |
| Untested `--safe-mode` / `--max-turns` flags | Runtime fallback: retry once without the flag, log a WARNING, and record it in state and pr.md (D5) | No |
| Project CLAUDE.md leaking into the model's context | `cwd=runs/<id>/` + `--safe-mode` (the user's decision) | Minor residual if safe-mode gets dropped |
| Tests pass alone but fail together | The combined run in `verify/` catches it; pr.md reports it and the exit code is 1 | No automatic remedy. Accepted |
| A target's own existing tests fail or none exist | The baseline records the result anyway (pytest exit 5 is treated as 0 tests); pr.md shows a warning | Accepted |
| Coverage `--branch` blends lines and branches in `percent_covered` | pr.md labels the number "coverage (lines + branches)" and also shows the statement percentage | No |
| Concurrent runs on the same run id | Not supported; one process per run. Documented | Accepted |
| A crash mid-stage | Atomic state save after every test transition; stages are idempotent, so resume continues | No |

## Out of scope
- git/GitHub, CI, any UI, non-Python targets, mutation testing, fixing target bugs (all from the brief).
- `--json-schema` output (D1), parallel test runs, branch-driven gap selection, an OS-level sandbox or container.
- Modifying or extending existing test files (the patch only adds new `tests/test_gen_*.py` files).
- **Evolve-stage artifacts** (`feedback/submissions.jsonl`, `evals/`, `docs/EVOLUTION.md`): deferred, pending open question 2. The pipeline's `state.json` and `pr/` folder already hold the data a later submissions record would need.
- A console-script entry point (`package = false`), so you run `python -m ctloop`.

## Amendments (user decisions, about 14:20). These override the sections above where they conflict
- **A1 Containment (replaces D12 option A):** pytest subprocesses run under macOS `sandbox-exec` with a profile that **denies network** and **denies file writes outside `runs/<id>/`** (plus the needed temp dirs, e.g. `/private/var/folders` and `/dev/null`). The environment is minimal: PATH, `PYTHONDONTWRITEBYTECODE=1`, and `HOME`/`TMPDIR` set to the run folder; no API keys or tokens are passed. If `sandbox-exec` is missing or the profile fails to apply, **fail closed** (exit 1) unless `--unsafe-no-sandbox` is given, which logs a WARNING and notes it in pr.md. Tests: the profile blocks a write outside the run folder and a socket connect (with the stub, no model).
- **A2 Wasted calls:** (a) a static **prefilter** in `gaps.py`. Skip gaps whose missing lines are only an `if __name__ == "__main__"` block, `# pragma: no cover`, `@abstractmethod`, a `...`/`pass`-only body, or a `Protocol` class body. These are reported as `skipped: untestable`, with no call made. (b) A **no-progress stop** in fix: if the failure signature (exception type + failing assert line/message) is identical to the previous attempt, quarantine right away with the reason `no progress`. The demo's `is_leap_year` can then quarantine at attempt 2. That's fine, and the "stops at cap" path is still shown by `--max-calls 4`, plus a unit test for the attempt cap.
- **A3 Model context:** the write prompt is the function's full source + docstring, the module's import block + the signatures of the same module's functions, its missing line numbers, **one** existing target test file as a style example, and the rules. Only content from inside the target, capped at about 8,000 characters (truncated with a marker). The fix prompt adds the current test + the last 40 lines of the failure output. Nothing from outside the target folder (no CLAUDE.md, env, or home files). Every prompt is saved to `calls/`.
- **A4 Filesystem bounds:** every write, move, or delete goes through `workspace.safe_path(run_dir, p)`, which resolves real paths and raises `PathEscapeError` unless the path is inside `runs/<id>/`. Run folders are created with `exist_ok=False` (they're never overwritten). Nothing outside the tool's own run folder is ever deleted, and there's no `clean` command. `copytree(symlinks=True)` copies links as links without following them. Tests cover escapes via `../` and via a symlink.
- **A5 Git:** local commits per slice are allowed (the user changed the brief at 14:15). No remotes, no push. The tool itself never calls git.
- **Confirmed by the user:** D1 plain fenced code, D2 one call per function, D3 `sonnet`, D4 pyproject + `uv sync`, Evolve deferred, exit 3 at a cap.
