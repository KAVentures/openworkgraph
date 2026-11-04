# Structured category evidence for reconstruction review

This optional local CLI converts **already authorized, explicit opaque category IDs** on observation metadata into scores for the existing production global review assignment. It eliminates a separate hand-authored event-to-tag JSON file when upstream consented observation data already carries these IDs. It does not infer categories from work text, access a provider, call an LLM, change canonical membership or authorize agent execution.

Input: a bounded local JSON list in the same event shape consumed by the reconstruction review queue. Optional `metadata.semantic_category_ids` is a list of at most 16 lower-case opaque IDs matching `[a-z][a-z0-9_-]{1,39}`. These IDs must have been approved for local use; they are **not** extracted from titles, URLs, raw content or private notes. Absence means no semantic signal. Invalid category claims and repeated event IDs contribute no category evidence. The existing production candidate queue and optimizer enforce the exact evidence digest, cross-scope and source-conflict vetoes, close-score abstention and bounded global disjoint matching.

Run locally:

```bash
python -m server.reconstruction_structured_evidence --events /path/to/consented-events.json --consent-local-structured-evidence
python -m unittest tests.test_reconstruction_structured_evidence
```

Output is JSON of **human-review proposals only**, with scored pair count, digest, number of category-bearing events, rejected category IDs and explicit `automatic_merge: false`. Do not upload local events or output without a separate grant. A category overlap is not a verified case, probability, source-authoritative identity or permission to act.

**Limitations and next milestone:** This is an input integration for existing structured categories, not automated extraction from rich text or a new production capture permission. It cannot recover cases without preexisting categories. No frozen family-holdout F1 improvement, contamination threshold, or consenting customer acceptance is claimed. Evaluate on development families before one sealed holdout and collect independently annotated real sessions.
