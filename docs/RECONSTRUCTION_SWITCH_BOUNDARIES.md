# Observed switch-aware interruption boundaries (Worker C / #326)

This local read-only algorithm identifies a potential resumption when **the same exact scoped business record** is observed, another record intervenes, and the first record returns (A → B → A). Unlike the idle-gap segmenter, it detects a short interruption even when the elapsed time is below the configured idle threshold.

Run from the repository root:

```bash
python -m server.reconstruction_switch_boundaries --events /path/to/consented-events.json --consent-local-review
python -m pytest -q tests/test_reconstruction_switch_boundaries.py
```

Input is a bounded JSON array of existing OWG events with event ID, timezone-aware observed time, organization ID and existing metadata.resource_reference (opaque reference, provider, kind, host). Only consented local observations should be used. Output contains opaque IDs, timestamps, counts and explicit review flags, never raw work text.

The algorithm sorts observations chronologically, suppresses duplicate event IDs and conflicting reference scopes, and abstains when timestamps tie because causal order is unknown. It emits an A→B→A boundary only when all three runs are observed. It does not join A and B, prove task completion, modify case membership, or authorize external actions. It is not a replacement for source-authoritative joins or the optional semantic linker in PR #599.

**Acceptance:** An observed short A→B→A sequence yields one reviewable resumption boundary; A→B→C, ambiguous timestamps, duplicates, conflicting scopes and missing consent cannot produce an unsafe boundary. The result is diagnostic evidence, not proof of assignment F1 improvement. Independent family-holdout accuracy, contamination, interruption rejection and consenting-user acceptance remain required.
