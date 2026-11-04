# R9 typed derived metric foundation (Worker A)

This isolated module supports deterministic, versioned arithmetic over **already authorized** numeric rows. It does not query or join event logs, authenticate users, persist expressions, publish data, or expose a dashboard endpoint. Those integrations and tenant/consent checks are required before claiming the R9 user-facing vertical slice.

A definition has `{"version":1,"expression":{"divide":[{"field":"cost"},{"field":"units"}]}}`. The caller passes an explicit allowlist of field names and a row already filtered for its tenant, role, consent and retention policy. `validate` returns a SHA-256 fingerprint of canonical definition JSON for lineage; it is **not** a dataset digest or authorization token. `evaluate` returns an exact decimal string, or null for missing operands and division by zero.

Supported nodes: `field`, `literal`, `add`, `subtract`, `multiply`, `divide`, `coalesce`. Each operator has exactly two operands. Limits: 64 nodes, depth 12, 32 allowlisted fields, 24-character numeric inputs. No `eval`, SQL, arbitrary functions, identifiers outside the allowlist, network calls or raw work text. Invalid expressions and nonfinite inputs fail closed. This version has no currency/unit compatibility, timezone expressions, derived joins, aggregate planner or UI. Results must not be represented as causal effects or verified business outcomes.

Run `python -m unittest tests.test_event_log_derived_metrics` from the repository root. Full native CI, authenticated integration, browser accessibility and real-provider acceptance are separate gates and are not satisfied by these tests.

## Bounded cohort summary

`summarize_rows(definition, rows, allowed_fields)` evaluates at most 1,000 already-authorized rows and returns only definition SHA-256, total/non-null/null counts, and exact decimal sum/mean/min/max. An empty or all-null cohort has null numeric statistics. The function rejects non-list input, excessive row counts, invalid row types and unapproved field references. It does not return source rows or persist cohort membership. **This is a pure Python analysis primitive, not an authenticated API, privacy aggregation threshold, dashboard feature, or production authorization boundary.** Callers must independently enforce consent, tenant isolation, retention, and minimum cohort size before disclosure.
