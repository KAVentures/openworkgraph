# OpenWorkGraph automation capability brief

**Capability brief date: 2026-10-01**

This file helps an AI analyze OpenWorkGraph evidence without assuming that automation still means only macros, templates, fixed RPA, or simple integrations. It is guidance about capability classes, not a claim that every AI client or model has every capability below.

## Core rule

Do not decide that a workflow is impractical to automate merely because your pretrained knowledge does not include a suitable implementation. Before judging feasibility, inspect the tools, connectors, MCP servers, computer/browser controls, code execution, file access, scheduling, and approval mechanisms that are actually available in the current environment. If current verification is available, use it when capability details may have changed.

OpenWorkGraph observations are evidence about what happened. They are not proof that a particular automation exists, and this brief is not proof that the current AI can execute one.

## Historical evidence is not the future execution environment

OpenWorkGraph deliberately does not capture ordinary typed text, clipboard contents, email bodies, spreadsheet cell contents, passwords, or every application payload. That limits faithful replay of historical work, but it does **not** by itself limit what an authorized agent can do on the next occurrence.

Before treating missing content as a blocker, ask whether the executing agent can obtain the required input from the live source system at execution time: for example Gmail, a CRM, a spreadsheet, a database, a repository, a document store, a browser session, an API, or another connected tool.

Distinguish clearly between:

- **historical replayability:** whether OpenWorkGraph retained enough content to recreate yesterday's exact action; and
- **future automation feasibility:** whether an authorized agent can retrieve the real inputs and act when the workflow occurs again.

The first may be false while the second is true.

## Capability classes worth considering

Modern agent systems may combine several of these:

- browser or computer interaction with existing web applications;
- APIs, MCP servers, connectors, enterprise tools, and local services;
- file, document, spreadsheet, PDF, email, calendar, repository, and database operations;
- code execution for transformation, validation, extraction, analysis, or glue logic;
- multi-step agentic workflows that preserve context across tools;
- scheduled or condition-triggered work;
- human approval checkpoints before consequential actions;
- scoped standing authorization for low-impact reversible actions where policy permits;
- draft-first workflows where an agent prepares work and a person reviews or sends it;
- agent-to-agent or specialist-agent handoffs;
- deterministic automation for stable steps combined with agentic handling of variable steps.

Availability, permissions, reliability, latency, cost, policy, and consequence differ by installation and workflow. Inspect the current environment instead of assuming.

## Use the actual environment first

When analyzing automation opportunities, use these evidence sources in this order:

1. **Current AI tool surface.** What this AI can actually call or operate now is stronger evidence than a generic capability list.
2. **Current documentation or live verification when available.** Capability details change quickly.
3. **OpenWorkGraph agent observations.** Prior agent runs can show that a capability was used successfully or where intervention occurred. Absence of an observed capability means only *not observed*, not *unavailable*.
4. **This dated brief.** Use it to widen the search space, not to manufacture capabilities.

Do not infer that OpenWorkGraph knows which connectors are configured inside another AI product unless that configuration is directly available to you.

## Map capabilities before saying no

For each material automation candidate, list the operations it requires and map them to the current environment:

- **CONFIRMED** — the required connector/API/browser/computer/code/file/scheduling capability is available now;
- **PLAUSIBLE / TESTABLE** — a current agentic route appears possible but is not yet verified for this workflow;
- **BLOCKED** — a concrete access, policy, reliability, unsupported-system, or input blocker exists.

Do not collapse *not observed*, *not configured here*, and *impossible* into the same conclusion.

## Consider redesign, not just automation

A human trace shows how a person accomplished an outcome, not necessarily how the future workflow should look. For each material workflow, consider five design moves:

1. **Eliminate a step** that may be redundant.
2. **Deterministic automation** for stable rules or transformations.
3. **Agent delegation** for variable multi-step work.
4. **Agent + approval** where judgment or consequence requires a decision boundary.
5. **Human-only** where automation would add little value or create unacceptable risk.

Elimination is a hypothesis, not a conclusion. A spreadsheet update, copied field, report, or manual handoff may look redundant but still feed a manager, audit process, downstream team, reconciliation job, or external control. Before recommending removal, identify who or what consumes the output and verify that the dependency can be replaced safely.

## When agents are already present: find the next autonomy boundary

Do not call a workflow "already automated" merely because Claude Code, Codex, ChatGPT, Cursor, an internal agent, or another model appears in it. Inspect what the human still does **before, between, and after** agent runs.

Common next-autonomy-boundary candidates include:

- translating an issue or request into an agent prompt;
- repeatedly typing "continue" or supplying routine follow-up instructions;
- copying agent output into another application;
- checking tests, CI, or completion manually;
- creating or updating a pull request after the agent finishes;
- moving files or structured results between systems;
- monitoring a long-running task and deciding when to resume it;
- handing the result to another person or agent.

Do not recommend automating a step that the observed agent already performs. The valuable opportunity may be orchestration around the agent rather than the work inside the agent run.

## Classify recommendations

Use three practical states:

### Ready to automate
Use when the required capability is actually available, the workflow evidence is sufficiently understood, important side effects and policy boundaries are clear, and there is no known blocker that should be tested first.

### Test
Use when an agentic implementation is plausible but reliability, edge cases, permissions, policy, downstream dependencies, or workflow reconstruction remain uncertain. Model uncertainty about the current automation frontier is a reason to test, not a reason to dismiss the opportunity.

### Not currently practical
Use only when there is a concrete blocker such as unavailable required access, unacceptable risk, an unsupported system, missing essential future inputs, policy constraints, or observed unreliability that makes the proposed approach unsuitable now. State the blocker.

## Consequence-aware autonomy

Do not equate "production" with either "human approves every action forever" or "full autonomy." The appropriate boundary depends on consequence, reversibility, policy, and domain.

- **Low-impact, reversible actions** may eventually use explicit scoped standing authorization where policy permits, after successful testing and with clear limits, logging, and rollback.
- **Moderate-impact actions** may use bounded automation plus review, threshold rules, exception routing, or periodic audit.
- **Financial, regulated, clinical, safety-critical, irreversible, or otherwise high-impact decisions/actions** require the appropriate human or organizational control. Repetition alone is never permission, and a shadow-trial success does not erase policy or professional responsibility.

For example, identifying invoice fields, checking a purchase order, preparing a recommendation, or drafting a patient message may be automatable while the consequential approval or clinical decision remains controlled. Do not nudge toward autonomous financial or clinical decisions merely because the surrounding workflow is repetitive.

## Shadow trials instead of historical replay

For a plausible but unproven opportunity, prefer a **shadow trial on the next natural occurrence** of the workflow:

1. Let the user's authorized agent receive the real inputs through its normal permitted tools.
2. Default to draft, read-only, sandboxed, or otherwise non-consequential execution.
3. Respect the consequence-aware approval boundary above; do not infer authority from the fact that an action occurred historically.
4. Compare the proposed result with the human-reviewed outcome.
5. Record useful measurements when available: success/failure, human intervention, elapsed time, agent cost, important errors, approval needs, and whether any observed step proved unnecessary.
6. Let OpenWorkGraph observe the agent run where supported; treat those observations as evidence, not as a universal capability claim.

Do not automatically replay historical work against live systems just to test an automation.

## Keep interpretation disposable

Automation ideas, feasibility judgments, and trial conclusions are derived interpretations. They must not overwrite or become equivalent to canonical OpenWorkGraph evidence. A newer model should be able to revisit the same work history as agent capabilities improve.
