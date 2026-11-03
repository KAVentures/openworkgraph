# Evidence-bound semantic assignment (Worker C, #326/#264/#370)

This opt-in local reconstruction command ranks **existing production ambiguous-link candidates** using separately supplied numeric semantic evidence. It uses a bounded maximum-weight matching algorithm, not greedy acceptance: each event endpoint participates in at most one proposed pair. A close competing score (less than 0.08 apart at either endpoint) causes abstention; pairs below 0.80 are withheld. Conflicting-record, cross-scope, tainted and otherwise ineligible production review candidates are rejected. It does not rewrite canonical membership or certify a business case.

## Operator contract

1. Obtain current explicit consent for the source observations and for any separate semantic scoring. Produce scores independently from **authorized, redacted** evidence; this module never reads or sends text, does not call an LLM, and cannot authenticate an external score producer.
2. Generate the current candidate manifest with `python -m server.reconstruction_review_queue --events observations.json`. Use its `evidence_sha256` in the score file.
3. Supply JSON `{"version":1,"evidence_sha256":"<digest>","pairs":[{"event_id":"e1","toward_event_id":"e2","score":0.91}]}`. Pair IDs must exist as an eligible candidate in the current production review queue. Numeric scores must be finite and in [0,1].
4. Run `python -m server.reconstruction_semantic_assignment --events observations.json --scores scores.json --consent`. The flag is an operator declaration, **not** proof of current consent; caller must enforce actual grant, retention and revocation. Outputs are suggestions requiring explicit review, never confirmed joins, permissions or source writes.

Limits: source size is capped by the existing reconstruction review reader, candidate queue by 500, score rows by 100 and optimization by 16 eligible endpoints. Overflow refuses rather than silently optimizing a truncated subset. The global matching maximizes the sum of eligible scores after safety abstention. It does not perform multi-hop case clustering. No persistent state or external provider access.

## Acceptance and remaining work

Focused tests exercise non-greedy optimal matching, ambiguity abstention, consent, stale evidence, malformed scores, conflicting candidate refusal and the actual production queue's empty-case digest. Required exact-head CI and real consenting-user tests are separate. This feature **does not** establish family-held-out assignment F1 ≥ 0.90 or contamination ≤ 0.05; score calibration, independently measured held-out accuracy, authenticated consent plumbing and eventual integration with a consented scoring provider remain open. Never treat numeric semantic scores as source-verified identity or permission.
