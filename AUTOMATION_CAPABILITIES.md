# OpenWorkGraph automation capability brief

**Capability brief date: 2026-09-30**

This file helps an AI analyze OpenWorkGraph evidence without assuming that automation still means only macros, templates, fixed RPA, or simple integrations. It is guidance about capability classes, not a claim that every AI client or model has every capability below.

## Core rule

Do not decide that a workflow is impractical to automate merely because your pretrained knowledge does not include a suitable implementation. Before judging feasibility, inspect the tools, connectors, MCP servers, computer/browser controls, code execution, file access, scheduling, and approval mechanisms that are actually available in the current environment. If current verification is available, use it when capability details may have changed.

OpenWorkGraph observations are evidence about what happened. They are not proof that a particular automation exists, and this brief is not proof that the current AI can execute one.

## Capability classes worth considering

Modern agent systems may combine several of these:

- browser or computer interaction with existing web applications;
- APIs, MCP servers, connectors, enterprise tools, and local services;
- file, document, spreadsheet, PDF, email, calendar, repository, and database operations;
- code execution for transformation, validation, extraction, analysis, or glue logic;
- multi-step agentic workflows that preserve context across tools;
- scheduled or condition-triggered work;
- human approval checkpoints before consequential actions;
- draft-first workflows where an agent prepares work and a person reviews or sends it;
- agent-to-agent or specialist-agent handoffs;
- deterministic automation for stable steps combined with agentic handling of variable steps.

Availability, permissions, reliability, latency, cost, and policy constraints differ by installation. Inspect the current environment instead of assuming.

## Use the actual environment first

When analyzing automation opportunities, use these evidence sources in this order:

1. **Current AI tool surface.** What this AI can actually call or operate now is stronger evidence than a generic capability list.
2. **Current documentation or live verification when available.** Capability details change quickly.
3. **OpenWorkGraph agent observations.** Prior agent runs can show that a capability was used successfully or where intervention occurred. Absence of an observed capability means only *not observed*, not *unavailable*.
4. **This dated brief.** Use it to widen the search space, not to manufacture capabilities.

Do not infer that OpenWorkGraph knows which connectors are configured inside another AI product unless that configuration is directly available to you.

## Classify recommendations

Use three practical states:

### Ready to automate
Use when the required capability is actually available, the workflow evidence is sufficiently understood, important side effects and approval boundaries are clear, and there is no known blocker that should be tested first.

### Test
Use when an agentic implementation is plausible but reliability, edge cases, permissions, or workflow reconstruction remain uncertain. Model uncertainty about the current automation frontier is a reason to test, not a reason to dismiss the opportunity.

### Not currently practical
Use only when there is a concrete blocker such as unavailable required access, unacceptable risk, an unsupported system, missing essential inputs, policy constraints, or observed unreliability that makes the proposed approach unsuitable now. State the blocker.

## Prefer outcome automation over click imitation

A human trace shows how the person accomplished an outcome, not necessarily the best way for an agent to accomplish it. Consider whether the same outcome can be produced through APIs, connectors, direct file operations, code, browser control, or a hybrid workflow instead of reproducing every observed click.

## Shadow trials instead of historical replay

OpenWorkGraph deliberately does not capture ordinary typed text, clipboard contents, email bodies, spreadsheet cell contents, passwords, or every application payload. Historical evidence therefore often lacks the full inputs required to replay a past task faithfully.

For a plausible but unproven opportunity, prefer a **shadow trial on the next natural occurrence** of the workflow:

1. Let the user's authorized agent receive the real inputs through its normal permitted tools.
2. Default to draft, read-only, sandboxed, or otherwise non-consequential execution.
3. Do not send messages, submit forms, change production records, purchase, delete, merge, deploy, or take another consequential action unless the user has explicitly authorized that action.
4. Compare the proposed result with the human-reviewed outcome.
5. Record useful measurements when available: success/failure, human intervention, elapsed time, agent cost, important errors, and where approval was needed.
6. Let OpenWorkGraph observe the agent run where supported; treat those observations as evidence, not as a universal capability claim.

Do not automatically replay historical work against live systems just to test an automation.

## Keep interpretation disposable

Automation ideas, feasibility judgments, and trial conclusions are derived interpretations. They must not overwrite or become equivalent to canonical OpenWorkGraph evidence. A newer model should be able to revisit the same work history as agent capabilities improve.
