# Backlog: Converging Test Loop

Logged 2026-11-24 at 14:46, at wrap-up. Each item comes from the brief's out-of-scope list, from the review, from the validation's "not tested" list, or from limits seen in real runs. Priority: **P1** next cycle, **P2** soon, **P3** later. Per the workflow, a picked item enters the pipeline at stage 1 (investigator).

## Integration
| # | Item | Why | Notes | Pri |
|---|---|---|---|---|
| 1 | **Open a real GitHub PR** | Today a "PR" is a folder. A real product opens a reviewable PR | A new `publish` stage behind a `Publisher` interface (the current folder publisher becomes the default). `GitHubPublisher`: make a branch `ctloop/<run-id>`, apply `changes.patch` in a clean clone, commit, push, then `gh pr create --draft` with `pr.md` as the body and labels for the quarantined tests. Needs an auth decision (gh CLI token or a GitHub App), a dry-run flag, and an idempotency rule (one PR per run id, and update rather than duplicate). It must still never push to the default branch | P1 |
| 2 | Comment on quarantined bugs | Suspected target bugs should reach the owners | Open a GitHub issue per quarantined function with the failure tail, linked from the PR | P2 |
| 3 | CI integration | Run on a schedule or on each push | A GitHub Action that runs `run-all` on a Linux runner (needs item 6 first) and uploads the PR folder as an artifact | P2 |

## Safety and containment
| # | Item | Why | Notes | Pri |
|---|---|---|---|---|
| 4 | Deny all reads under HOME except an allowlist | Today only known secret folders are blocked (README "Known risks"), and test output reaches the model in fix prompts | Allowlist the venv, uv's Python, and the run folder; test that pytest still works | P1 |
| 5 | Redact secrets in failure output before it goes into prompts or pr.md | Defense in depth for item 4 | Regex for common token formats | P2 |
| 6 | A portable sandbox (container or VM) | `sandbox-exec` is macOS only and deprecated | Docker or bubblewrap/nsjail on Linux, behind the same `Executor` interface | P2 |
| 7 | A run lock | Two processes resuming the same run id isn't supported | An exclusive lock file in `runs/<id>/` | P3 |

## Test quality
| # | Item | Why | Notes | Pri |
|---|---|---|---|---|
| 8 | Catch a changed expected value | The guard's known gap: a fix can keep the same number of assertions but flip what they expect | Compare the fix's expected values against docstring examples, or require the expected values to stay the same between attempts | P1 |
| 9 | Quarantine by root cause | In the real run, `days_in_month` was quarantined only because it calls the buggy `is_leap_year` (coverage 58.5% against the stub's 92.5%) | Use the call graph to link failures to a single root bug, and suggest regenerating the dependent test with non-triggering inputs | P1 |
| 10 | Mutation testing to score generated tests | Coverage doesn't prove the assertions are strong | Out of scope for this build. mutmut or cosmic-ray on the patched copy | P3 |
| 11 | Detecting flaky tests | A test that passes once may be flaky | Re-run each passed test N times in `verify/` | P2 |
| 12 | Branch-coverage-driven gaps | `missing_branches` is already collected but not used | Target partial branches, not only missing lines | P3 |

## Cost and performance
| # | Item | Why | Notes | Pri |
|---|---|---|---|---|
| 13 | Estimate spend before each call | The spend cap can overshoot by one call (about $0.25) | Estimate from prompt size, and refuse the call if the estimate goes over the cap | P2 |
| 14 | Run tests in parallel | They run one after another today | A worker pool for each test file, keeping the per-file sandbox | P3 |
| 15 | `--json-schema` structured output | Plain fenced-code extraction is heuristic (D1) | Offer it as a provider option once verified | P3 |

## Validation gaps (untested live)
| # | Item | Pri |
|---|---|---|
| 16 | A real run that hits the $2 spend cap (only unit-tested) | P2 |
| 17 | The `--safe-mode` fallback (the flag never errored) | P2 |
| 18 | A real hung call that hits the timeout | P2 |
| 19 | A fresh clone followed by `uv sync` setup | P1 |
| 20 | The README's real-run example uses `--max-calls 10`, but only 5 was validated | P3 |

## Workflow and process
| # | Item | Why | Pri |
|---|---|---|---|
| 21 | Evolve stage: `feedback/submissions.jsonl` + `evals/` | Deferred at planning. Needed for the eval-driven loop (pass rate, calls, time before and after) | P2 |
| 22 | Parallel build agents | Freeze the interfaces (`Provider`, `Gap`, `RunState`, `CoverageResult`) and give each module its own agent and git worktree | P3 |
| 23 | Bigger and more varied targets | One 6-file sandbox doesn't show how this scales | Classes, async code, I/O-heavy functions, a real OSS package | P2 |
