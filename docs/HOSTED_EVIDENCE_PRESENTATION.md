# Hosted MCP evidence presentation contract

These rules apply to both Claude and ChatGPT over the **same OAuth-protected OWG MCP server**. This document does not grant any new access or affect local capture.

## Owner identifiers and redaction

**Policy: tokenize the authenticated owner's own identifiers in every model-facing hosted MCP tool**, including profile, observed titles, target labels, metadata, goal examples and optional shared-text snippets.

- `get_profile.id` stays a stable opaque account identifier. `get_profile.name` is an `OWNER_…` token when identity name hints are available; `get_profile.email` is `null` (not a fabricated email address).
- The public redactor uses identity hints from the *validated* OAuth token, solely to detect the owner in observed text. This never changes permission checks or actor/tenant scoping.
- Known emails become `EMAIL_…`; owner aliases become `OWNER_…`. Unknown free-form observed titles and target captions now **fail closed** to stable `TITLE_…` / `TARGET_…` tokens, including nested target metadata. Recognizable bounded UI action labels remain readable. `ORG_…` covers company-suffix expressions. This is deliberately more conservative than person-name dictionaries.
- Original evidence is kept in the authorized store. Exact title/target searches match approved observed fields server-side, returning opaque results rather than leaking the original captions. Other fields or separately opted-in shared text need their own privacy review.
- Redaction is a presentation transformation only. Original Gateway evidence is not rewritten or retroactively rescanned. The local application retains its existing access policies.

## Repeated workflow candidates

Human candidate windows can cover unrelated websites, but genuine workflows can also cross Gmail, Sheets, and CRM. Hosted **discovery now retains unsplit session windows** and ranks by distinctiveness rather than treating host changes as task boundaries. Selected execution lookup still accepts split legacy references where available. Neither split nor unsplit grouping is verified business task identity; inspect the unfiltered raw trace.

Each cluster has `cluster_quality` between 0 and 1. This is a **structural specificity score, not probability or confidence that the executions share a business task**. Generic `action:click` / `action:scroll` and single-surface similarity score low. Shared copy/paste, edit, submit or agent tool sequences are more informative.

Hosted `find_repeated_workflows` **includes low-specificity candidates by default**, explicitly reports their count and ranks distinctive patterns first. A caller may still pass `include_low_specificity=false` as an intentional precision filter, but that must never be confused with evidence absence. Canonical `get_workflow_trace` is unaffected. The local workflow behavior remains unchanged.

`surface_labels` maps each structural `surface:…` key to a vetted readable product surface where safely known; unknown/hard-to-identify surfaces remain `Other surface (redacted)`, not a guessed vendor.

## Complete evidence drilldown

`get_workflow_evidence` deliberately excerpts 12 events per execution in compact mode (100 in rich mode), rather than returning unlimited raw rows. An excerpt marked `events_truncated=true` now contains a `trace_lookup` with `since`, `until` and `session_id` if a single session is known. Call `get_workflow_trace` with those filters and follow its `next_cursor` while `has_more` is true. Use **no query** so unmatched surrounding actions are not dropped. Human boundaries and membership remain inferred even with exact filters; verify events against the source system before substantive claims.

## Payload size

Existing `detail="compact"` and `detail="rich"` remain supported. Compact now omits repeated explanatory prose while retaining observed evidence, pagination, suppression counters, and required boolean authority flags. The **opt-in** `detail="minimal"` mode is even smaller and is accepted by tools already exposing a `detail` parameter. It projects compact events and elides repetitive explanatory fields, keeping evidence, IDs, pagination, flags and a short `contract_version` marker. Static safety instructions remain in the MCP server's instructions. Minimal does **not** weaken authentication, text-consent checks or privacy redaction. Use compact/rich if existing clients rely on verbose contracts.

## Historical goal search and coverage

`get_goal_context` samples the latest 500 actor-scoped synced rows plus up to four lexical probes over the **full permitted synced time range**. Each probe now pages up to 600 matching events; this is still bounded and does **not** guarantee recall. The response explicitly includes:

- `recent_tail_capped`, `lexical_probes_capped`, and `source_limit_reached`;
- `lexical_search_indexed=false` (the current LIKE search does not have a dedicated full-text index);
- `all_matching_history_found=false` and `complete_work_history=false`.

Every goal-context response also includes up to ten query-free recent raw events even when inferred or lexical examples are empty. A missing result cannot prove the work never happened. For older or uncertain cases, perform date-bounded, query-free `get_workflow_trace` pagination or use a connected source-system search. Do not silently infer that the 500-event recent tail is the entire historical record.

## Release gate

Test these contracts with synthetic users in the real Claude and ChatGPT connector sessions: OAuth and stable profile ID; profile/name/title consistency, foreign-account isolation; generic click false positives; real distinct workflows; truncated 100+ event drilldowns; stable cursor pagination; compact/rich/minimal compatibility; same-origin tool selection; optional work text disabled/enabled; and empty or sparse history. Only deploy after the repository's cross-platform regression suite passes.
