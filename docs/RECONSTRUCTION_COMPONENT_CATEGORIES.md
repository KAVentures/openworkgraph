# Corroborated component-category reconstruction (R1)

Run locally from the repository root with an existing authorized observation export:

```bash
python -m server.reconstruction_component_categories --events /path/to/observations.json --consent-local-categories
```

This optional, read-only scorer takes previously approved opaque `metadata.semantic_category_ids` on observations. It first groups observations **only by deterministic production source links**, then requires each category to appear in at least two distinct event IDs within each component. Candidate pairs must share two independently corroborated categories. Corpus-frequency-weighted Jaccard similarity suppresses ubiquitous categories, and the existing review queue, risk vetoes, evidence digest, global nonoverlapping matching, 0.80 threshold and 0.08 ambiguity margin remain in force.

Unlike endpoint-only matching, an ambiguous bridge between two multi-observation components can be proposed even when the two endpoint observations carry no matching categories, provided the components each independently corroborate the same two categories. An ambiguous candidate is never used to form a source component. Duplicate event IDs and invalid category metadata contribute no category evidence.

The CLI requires explicit local structured-category consent and emits review proposals only. It does **not** read work text, call a model or provider, verify business-case identity, change canonical membership, or authorize execution. Do not use this as a production probability or verified case assignment. No retention or revocation rights are extended by this command; inputs must already be authorized for local processing. Compare this scorer against the endpoint-only baseline on independently labeled development families, then a once-only frozen family holdout. The required assignment F1 >= 0.90, contamination <= 0.05, interruption rejection and real consenting-user acceptance remain unproven.
