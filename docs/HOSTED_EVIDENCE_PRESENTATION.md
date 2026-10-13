# Hosted MCP evidence presentation contract

These rules apply to both Claude and ChatGPT over the **same OAuth-protected OWG MCP server**. This document does not grant any new access or affect local capture.

## Owner identifiers and redaction

**Policy: tokenize the authenticated owner's own identifiers in every model-facing hosted MCP tool**, including profile, observed titles, target labels, metadata, goal examples and optional shared-text snippets.

- `get_profile.id` stays a stable opaque account identifier. `get_profile.name` is an `OWNER_…` token when identity name hints are available; `get_profile.email` is `null` (not a fabricated email address).
- The public redactor uses identity hints from the *validated* OAuth token, solely to detect the owner in observed text. This never changes permission checks or actor/tenant scoping.
- Known emails are tokenized as `EMAIL_…`; owner first/full-name variants are tokenized to the same account-scoped `OWNER_…`. Unrecognized third-party names are handled by existing conservative detection. Not all personal names can be identified perfectly from telemetry; users should still review any optional shared text.
- Redaction is a presentation transformation only. Original Gateway evidence is not rewritten or retroactively rescanned. The local application retains its existing access policies.

## Repeated workflow candidates

Human candidate task windows can cover unrelated websites. The derived *execution* projection now splits on observed host or nonbrowser-app switches where present; it never removes canonical events. Inferred membership remains unverified. Some same-host but unrelated tasks may still be grouped, and rapid navigation can over-split a legitimate multi-site task. Never claim task identity or completeness based only on a cluster.

Each cluster has `cluster_quality` between 0 and 1. This is a **structural specificity score, not probability or confidence that the executions share a business task**. Generic `action:click` / `action:scroll` and single-surface similarity score low. Shared copy/paste, edit, submit or agent tool sequences are more informative.

Hosted `find_repeated_workflows` suppresses clusters scoring below 0.45 by default, reports `suppressed_low_specificity_clusters`, and accepts `include_low_specificity=true` to inspect them. This affects only discovered *derived candidates*. `search_work` and canonical `get_workflow_trace` still access all authorized source events. Local workflow clients retain their previous unfiltered default.

`surface_labels` maps each structural `surface:…` key to a vetted readable product surface where safely known; unknown/hard-to-identify surfaces remain `Other surface (redacted)`, not a guessed vendor.

## Complete evidence drilldown

`get_workflow_evidence` deliberately excerpts 12 events per execution in compact mode (100 in rich mode), rather than returning unlimited raw rows. An excerpt marked `events_truncated=true` now contains a `trace_lookup` with `since`, `until` and `session_id` if a single session is known. Call `get_workflow_trace` with those filters and follow its `next_cursor` while `has_more` is true. Use **no query** so unmatched surrounding actions are not dropped. Human boundaries and membership remain inferred even with exact filters; verify events against the source system before substantive claims.

## Payload size

Existing `detail="compact"` and `detail="rich"` behavior remains supported. A new **opt-in** `detail="minimal"` mode is accepted by the historical-work and evidence tools that already have a `detail` parameter. It projects compact events and elides repetitive explanatory fields, keeping evidence, IDs, pagination, flags and a short `contract_version` marker. Static safety instructions remain in the MCP server's instructions. Minimal does **not** weaken authentication, text-consent checks or privacy redaction. Use compact/rich if existing clients rely on verbose contracts.

## Historical goal search and coverage

`get_goal_context` samples the latest 500 actor-scoped synced rows plus up to four lexical probes over the **full permitted synced time range**. Each probe now inspects up to 180 matching events; this is still bounded and does **not** guarantee recall. The response explicitly includes:

- `recent_tail_capped`, `lexical_probes_capped`, and `source_limit_reached`;
- `lexical_search_indexed=false` (the current LIKE search does not have a dedicated full-text index);
- `all_matching_history_found=false` and `complete_work_history=false`.

A missing result cannot prove the work never happened. For older or uncertain cases, perform date-bounded, query-free `get_workflow_trace` pagination or use a connected source-system search. Do not silently infer that the 500-event recent tail is the entire historical record.

## Release gate

Test these contracts with synthetic users in the real Claude and ChatGPT connector sessions: OAuth and stable profile ID; profile/name/title consistency, foreign-account isolation; generic click false positives; real distinct workflows; truncated 100+ event drilldowns; stable cursor pagination; compact/rich/minimal compatibility; same-origin tool selection; optional work text disabled/enabled; and empty or sparse history. Only deploy after the repository's cross-platform regression suite passes.
