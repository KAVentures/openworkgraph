# Discovery Mode

Discovery Mode is an optional, temporary workflow-discovery layer for AI/automation implementations. It reuses OpenWorkGraph's canonical evidence store and workflow-evidence tooling; it does not create a second collector, replace normal OWG capture, or make inferred tasks authoritative.

## Intended use

A customer or implementation team can ask a worker to run a purpose-limited study such as:

> Map the customer-pricing workflow for five working days so the implementation team can understand the real path, common variants and unresolved business rules.

The employee chooses the native apps and/or browser hosts in scope. Discovery Mode then:

1. applies that **positive allowlist before canonical event persistence**;
2. observes only the configured study window;
3. keeps ordinary workflow groupings as non-authoritative navigation aids;
4. suggests targeted questions from observed structural variations;
5. stores employee answers separately as attributed human statements;
6. requires employee review before a Discovery Package can be exported;
7. never treats observed repetition as policy, permission or authorization.

When Discovery Mode is inactive, the new persistence filter is a no-op and normal OpenWorkGraph behavior is unchanged.

## Browser scoping

Browser hosts should normally be specified explicitly, for example:

- `mail.google.com`
- `docs.google.com`
- `*.salesforce.com`

A generic browser application such as Chrome, Safari, Edge or Firefox does **not** authorize unresolved browser pages by default. Desktop browser events whose active host cannot be proven are dropped while a strict Discovery study is active.

There is an explicit **Keep unresolved browser-container events** switch for deployments that accept the broader collection boundary. It is off by default.

## Time window and retention

A Discovery study has an explicit start and end. Evidence outside that window is not persisted under the Discovery scope. After the end time, the study moves to review and later events remain outside the Discovery collection window until the user exits Discovery Mode.

Detailed canonical evidence from the Discovery window has a separate deletion horizon (14 days after the study by default, configurable from 1–90 days). Cleanup runs at startup and when Discovery status is read. The employee can also delete the Discovery-window canonical evidence immediately from the review UI.

This Discovery cleanup applies to canonical `events` and their normalized/context derivatives. Visible agent-session message storage remains a separate, explicit opt-in channel with its existing independent retention policy.

## Organization Gateway

Discovery evidence is not supposed to bypass employee review through normal organization synchronization.

If the endpoint is already connected to a Gateway when Discovery starts, Discovery Mode pauses organization sharing using the existing pause/no-backfill mechanism and remembers whether sharing was already paused.

In addition, Gateway sync reads the Discovery start cursor boundaries. While the study remains active or in review, canonical events and opt-in agent-session messages created after those boundaries are processed locally but are not uploaded through normal Gateway sync. This protects a study even if Gateway connectivity changes after the study begins.

Exiting Discovery Mode restores ordinary Gateway sharing only when Discovery Mode itself paused it. A pre-existing employee pause is preserved.

The approved Discovery Package is a local export. Exporting it does not upload it anywhere.

## Employee review

At review time the employee can:

- include or exclude candidate executions from the package;
- answer evidence-grounded questions about structural variants;
- download a redacted preview JSON;
- explicitly approve and download the final redacted Discovery Package;
- delete the study-window canonical evidence immediately.

Excluding an execution from a Discovery Package does **not** alter or relabel canonical evidence. Employee answers are stored in the Discovery state file, not in the canonical event table.

## Discovery Package

The package is intentionally evidence-first. It includes:

- study purpose, scope and observation window;
- coverage and explicit limitations;
- selected workflow-evidence bundles with canonical provenance;
- separately attributed employee statements;
- unresolved/suggested questions;
- a handoff note for an authorized AI or implementation team.

The package explicitly states that structural traces are **not replayable business test inputs**. OpenWorkGraph does not capture ordinary typed values or clipboard contents. Source-system test data or synthetic fixtures are still required for executable agent evaluation.

## What Discovery Mode does not do

Discovery Mode does not:

- decide what a workflow "really means";
- convert repeated behavior into policy or authorization;
- automatically deploy an agent;
- automatically send data to an AI provider;
- automatically share the package with an organization;
- claim that a short study observed rare/seasonal exceptions.

Use `get_workflow_evidence` or the Discovery Package with an authorized external AI/implementation process to draft an agent-neutral procedure, then resolve business rules and approval boundaries with the people who own them.
