# Event-to-resource-pair evaluation (not business-case accuracy)

The review queue is a proposal/abstention surface, not a case classifier. To
measure proposals, create an **independently labeled, sealed holdout** of
explicit event identities and expected exact resource pairs. Gold JSON is a
list of objects with `window`, `event_id`, and `expected_pair`. A pair is
two pointer objects containing `provider`, `resource_kind`, and
`resource_ref`; use `null` only for events explicitly labeled unrelated.
Include *every* evaluated event, including unassigned negatives. Use exactly
the window strings in the review queue (for example `current` or
`earlier:2026-11-01T09:00:00Z`).

```bash
python -m server.reconstruction_review_evaluation review.json sealed-gold.json
python -m unittest tests.test_reconstruction_review_evaluation
```

The evaluator refuses unlabelled proposals, duplicate event labels, malformed
pairs, confirmed-case claims and truncated coverage. A multiple-pair proposal
abstains; it cannot be counted as a correct assignment. Metrics are micro
precision, recall, F1 and wrong-positive-to-other-case contamination count
over **exact event/pair associations**, not case/workflow accuracy. The
`held_out_verified` field is always false because the program cannot prove
a dataset was sealed or independently labeled; record that externally.

Do not export gold or review data beyond the original authorized
consent/tenant/retention scope. Never report the synthetic regression fixtures
as held-out performance. The work-resumption API integration and the product
review UI require separate acceptance and current-main CI validation.
