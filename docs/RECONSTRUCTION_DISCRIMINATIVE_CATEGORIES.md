# Discriminative structured-category case review

This opt-in local reconstruction feature builds on the existing production candidate queue and global assignment optimizer. Instead of treating every approved opaque semantic category equally, it estimates document frequency from the current bounded consented event batch and computes a weighted Jaccard similarity. A category present on at least 75% of four or more distinct eligible observations is suppressed as too generic. Other categories receive weight `1 + log((N + 1)/(df + 1))`. At least two distinct shared, non-suppressed categories are required. The existing optimizer additionally requires score >= 0.80 and rejects close competing alternatives.

Run locally with an already authorized bounded JSON event list containing optional `metadata.semantic_category_ids` arrays of approved opaque identifiers:

```bash
python -m server.reconstruction_discriminative_categories --events /path/to/consented-events.json --consent-local-categories
python -m unittest tests.test_reconstruction_discriminative_categories
```

This is **review-only**. The system scores only existing production reconstruction candidates and retains their evidence digest, scope/source vetoes, bounded global disjoint matching and ambiguity abstention. Duplicate event IDs and malformed category claims contribute no semantic evidence. It reads no raw text, URL, clipboard, provider or LLM; it never creates case membership or agent authorization. Local consent does not grant permission to export events.

**Operational caveats:** IDF is computed within the current input batch, not from a learned customer corpus. Suppression may reduce recall on small or homogeneous work sets; a shared generic category is not proof of case identity. Category scores are not calibrated probabilities. Changing the batch changes scores and requires fresh review. Do not use output as an automatic join.

**Acceptance still needed:** compare against the unweighted structured category scorer on separately annotated development families, record precision/recall/contamination and interruption rejection, then evaluate exactly once on the frozen family holdout without tuning on it. Required targets remain assignment F1 >= 0.90 and contamination <= 0.05, plus independent consenting-user pilot validation. This milestone does not claim those targets.
