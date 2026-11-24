---
name: michael-dev-workflow
description: Runs the full agent pipeline for a build task. Brief, then investigator (user approves), planner (user approves), implementer, then an implement↔review loop capped at 10 minutes (stops early on questions), then validator after user approval. Every agent follows this workflow. Use when the user gives a build task or says "michael-dev-workflow".
---

# Michael Dev Workflow

The main session runs this workflow and hands each stage to a subagent: `investigator`, `planner`, `implementer`, `reviewer`, `validator`. Every agent follows **this workflow**, which is preloaded through its frontmatter. Also restate it in each handoff prompt: "Follow michael-dev-workflow, the steps that apply to your role."

## Rules for every stage
- **Keep the mission in view.** `docs/00-brief.md` records why we're doing this, the goal, and how we'll do it. Every handoff prompt starts by telling the agent to read it.
- **Don't guess. Ask on tradeoffs that no constraint settles.** If an agent returns `## Open questions`, **stop** and put them to the user with AskUserQuestion, one decision per question with the recommended option first. Record the answers in the brief or the plan, then continue.
- **Handoffs carry context:** each prompt names the docs to read, what the user just approved or decided, and the exact deliverable.
- **Log the user's guidance:** append a row to `docs/guidance-log.md` every time the user approves, corrects, or decides something.
- After each stage, tell the user in 5 lines or fewer what came back and what you need from them.

## Stages

### 0. Preflight and brief (main session)
Run a preflight check (tools, repo state, time budget, stack). Grill the user, one question at a time with a recommendation each, then write `docs/00-brief.md` covering:
**Why**, **Goal**, **How**, **Constraints**, **Time budget**, **Definition of done**, **Out of scope**.
→ **GATE: the user approves the brief.**

### 1. Investigate
Launch `investigator`. It produces `docs/01-investigation.md`.
→ Present the summary, the key findings, and any open questions.
→ **GATE: the user approves the investigation.** Don't hand it to the planner before that.

### 2. Plan
Launch `planner` with the approved investigation and any user decisions. It produces `docs/02-plan.md`.
→ Present the architecture, each decision with its pros and cons and the recommendation, and the risks.
→ **GATE: the user approves the plan**, including their picks on any open decisions. No code before this.

### 3. Implement (first build, not timed)
`git init` if needed. Launch `implementer` to build the approved plan. Then report what was built, along with the test and run output.

### 4. Implement ↔ review loop (time-boxed: 10 minutes)
1. Record the start time **when the reviewer first launches**: `date +%s` → `LOOP_START`.
2. Launch `reviewer` and read `docs/04-review.md`.
3. Decide what happens next:
   - **Verdict APPROVE with no findings** → the loop is done. Go to the validation gate.
   - **Open questions** → **stop the loop** and ask the user. Continue only once they answer, and the clock keeps running.
   - **Findings but no questions** → launch `implementer` to fix them. In-loop fixes don't need user approval. Then go back to step 2.
4. **Before starting each new agent in the loop**, check the time with `echo $(( $(date +%s) - LOOP_START ))`. If 600 seconds or more have passed, don't start another one. An agent that's already running is allowed to finish, so the code is never left half-edited. Then stop.
5. **No progress:** if a round of review repeats the same findings as the round before, stop and ask the user. Don't keep cycling.
6. When the loop ends, report how many rounds ran, the time used, the findings that were fixed, any that remain, and why the loop stopped (clean, questions, time limit, or no progress).
→ **GATE: the user approves moving on to validation**, or tells you how to handle the remaining findings.

### 5. Validate
Launch `validator`. It produces `docs/05-validation.md`.
→ Present the verdict and any failures. For each manual check, **walk the user through the steps one at a time** and record what they see.
→ Failures go back to the `implementer` only with the user's approval, then the validator runs again.

### 6. Evolve (eval-driven loop): **started by the dev only**
Product rule: when a user (or the `user` agent) submits code, **only the pipeline runs** (pre-check → gaps → write → run → fix → gate). No workflow agents run. Each submit appends a record to `feedback/submissions.jsonl`.
Investigation, planning, and everything else in this workflow run **only when the dev (the human user) starts them**, never because of a submission. When the dev starts an evolution cycle:
1. The validator appends structured feedback to `docs/EVOLUTION.md`, and new scenarios are saved as eval cases in `evals/`.
2. **GATE: the user picks** which EVOLUTION items to work on.
3. Each picked item runs through this workflow from stage 1, the investigator, with the EVOLUTION entry as its input.
4. After the change, the validator re-runs **all** eval cases and reports before vs after (pass rate, calls, time). Regressions block it from counting as done.

### 7. Wrap up
Give the user a final walkthrough they can present: the architecture, the key decisions and tradeoffs, the current status, and what you'd do next.
