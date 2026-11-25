# Guidance log

| Time | Stage | Decision / guidance |
|---|---|---|
| 14:00 | Brief | Task: converging test loop. Python only, no git/GitHub (a PR is a folder with a patch + pr.md), free/OSS only besides Claude Code, never touch target source, caps on calls/retries, run tests on a copy. 60 min total, demo by min 50, review loop max 5 min. |
| 14:02 | Brief | Model: `claude -p` for real runs + a deterministic stub provider for tests and offline use. |
| 14:02 | Brief | Tooling: uv venv + pytest + coverage.py. |
| 14:02 | Brief | Failing test: fix the test up to the cap, then quarantine it and report it in pr.md as a suspected target bug. |
| 14:02 | Brief | Sandbox gets 1 seeded bug. Default caps: 30 model calls, 3 fix attempts per test. |
| 14:06 | Brief | User approved the brief (one PR folder per run, 5-min review loop, no git init). |
| 14:08 | Investigate | User approved the investigation; the 4 small choices (output format, call granularity, model, install method) get decided in the plan. |
| 14:11 | Investigate | Spend caps: --max-budget-usd 0.25 per call, $2 per run (tracked via total_cost_usd, clean stop), both set via CLI flags. |
| 14:11 | Investigate | Demo: deterministic stub provider, plus one real claude -p run if time allows (stub as the fallback). |
| 14:11 | Investigate | Isolation: cwd=run folder AND --safe-mode; if --safe-mode errors, fall back to cwd only and log a warning. |
| 14:11 | Investigate | Validator may do ONE small real claude -p run (about 5-call cap); everything else uses the stub. |
| 14:16 | Plan | D1 plain fenced code; D2 one call per function; D3 sonnet default; D4 pyproject + uv sync (fall back to uv pip). |
| 14:16 | Plan | Evolve files (submissions.jsonl, evals/) deferred to README "Next steps". Hitting a cap exits with code 3. |
| 14:16 | Plan | .git (appeared at 14:04): keep it, commit each slice LOCALLY. Still no GitHub, no remote, no push. Brief amended. |
| 14:22 | Plan | Containment: sandbox-exec (no network, no writes outside runs/<id>) + minimal env; fail closed unless --unsafe-no-sandbox. |
| 14:22 | Plan | Waste: prefilter untestable gaps + a no-progress early quarantine when the failure signature repeats. |
| 14:22 | Plan | Model context: function source + module header/signatures + 1 existing test example, about 8k char cap; fix prompts add the test + the failure tail. |
| 14:22 | Plan | FS bounds: central safe_path guard inside runs/<id>, never overwrite run folders, never delete outside, symlinks not followed. |
| 14:22 | Plan | User approved the plan with amendments A1-A5. Implementation started. |
| 14:24 | Implement | User confirmed: commit locally at each slice checkpoint. |
| 14:32 | Implement | Build done (47 tests pass, stub demo 28.3%->92.5%, real run 28.3%->58.5% with $0.069 spent). Review loop started (5-min cap). |
| 14:37 | Review | Loop done: 2 rounds, verdict APPROVE. B1/B2 (sandbox profile tamper, run-state writes), D1, D2 fixed in 0819a2b. |
| 14:38 | Review | User approved moving to validation. |
| 14:40 | Validate | PASS WITH ISSUES: all 6 DoD items pass; real 5-call run 28.3%->58.5%, $0.035, exit 3. I1: stale sandbox.sb in README:59 (awaiting user). |
| 14:41 | Validate | User approved README-only fix for I1; committed. |
| 14:42 | Validate | Manual step 1: user ran pytest and saw 50 passed. |
| 14:43 | Validate | Manual step 2: stub run-all 20261124-144322-2d700f gave 28.3%->92.5%, 1 quarantined, 4 passed, exit 0. |
| 14:46 | Validate | Manual step 3: pr.md shows coverage, tests added, quarantine, and integrity PASS. |
| 14:47 | Validate | Manual step 4: changes.patch has 4 new-file diffs, no is_leap_year. |
| 14:50 | Wrap-up | User skipped manual steps 5-6 (the validator already covered them) and asked for a walkthrough + backlog (incl. GitHub PR integration). Wrote docs/BACKLOG.md. |
