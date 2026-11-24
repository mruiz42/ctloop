# CLAUDE.md

## Workflow: agent pipeline (run it with the `michael-dev-workflow` skill)
This project is built through 5 subagents in `.claude/agents/`, run in this order:

0. **Brief:** the main session writes `docs/00-brief.md` with the user (why we're doing this, the goal, how we'll do it, constraints, the time budget, and what "done" means). Every agent reads it first.
1. `investigator` writes `docs/01-investigation.md`. **The user approves it before planning starts.**
2. `planner` writes `docs/02-plan.md`. **The user approves the plan before any code is written.**
3. `implementer` builds the plan in thin slices, with tests and small commits
4. `reviewer` writes `docs/04-review.md` (big picture first, then details). Implementer ↔ reviewer then loop until the review is clean, **capped at 10 minutes** (the timer starts at the first review, and an agent already running is allowed to finish). The loop stops early on open questions. **The user approves moving on to validation.**
5. `validator` writes `docs/05-validation.md`, plus a manual-validation guide for anything it can't run itself

## Rules for every stage
- When in doubt, don't guess: ask the user first.
- When a tradeoff isn't settled by a given constraint, ask the user first.
- Subagents can't ask the user directly. They return an `## Open questions` section, and the main session must put those questions to the user before moving on.
- Always remember why we're doing the task, what goal we're aiming at, and how we want to do it. That's `docs/00-brief.md`.
- Every agent follows `.claude/skills/michael-dev-workflow/SKILL.md`, the parts that apply to its role.
