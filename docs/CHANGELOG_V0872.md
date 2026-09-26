# OpenWorkGraph v0.87.2

This patch makes compact procedural feedback useful to an AI client without changing the stable procedural identity layer introduced earlier.

## Readable human feedback

`how_did_similar_runs_go` can now present human procedures with privacy-safe semantic steps such as:

```text
Gmail · Open email
Salesforce · Open account
Google Sheets · Update status
Gmail · Send
```

These labels are derived from the existing allowlisted/pseudonymized work-surface vocabulary and safe action labels. Arbitrary names, emails, URLs, tenant hosts, paths and free text are not copied into the readable procedure.

## Stable identities remain unchanged

Readable steps are a presentation and matching layer only. Existing procedural-memory structural steps such as `surface:gmail` and `action:click` continue to define structural family identities. v0.87.2 does not migrate, rewrite or reinterpret existing `family_key` values.

Legacy structural step input remains supported. Human-readable progress is accepted only when it exactly matches a privacy-safe semantic step already observed for that family; unknown readable steps return the valid observed step names rather than being guessed.

## Smaller compact context

`get_current_work_context` now returns a summarized first-pass trace instead of embedding full rich event metadata. `get_workflow_trace` remains the canonical evidence tool and keeps the full row contract, but its default page is smaller for agent context budgets. Callers can still request larger pages and follow `next_cursor`.

## Authority boundary

All prior-run feedback remains derived, non-authoritative and non-prescriptive. Repeated behavior is not policy, permission, proof of correctness or an instruction to repeat the procedure.
