# R22: consented communications active-review queue

The Communications panel now has **Prioritize human review** within the separately enabled **Review examples and learned model** section. It orders existing, redacted, unlabelled examples for a human reviewer. No new source export is collected, and no change to the default data-retention or capture policy is made.

## Operation

1. The user explicitly opts in to keeping selected redacted review examples and chooses a retention period from 1 to 90 days. Without this consent the queue refuses access.
2. The queue considers only *unlabelled training-split* examples from that consented store. **Holdout examples are excluded from priority selection** to keep the held-out evaluation independent of active selection.
3. Keyword taxonomy matches are calculated over the existing redacted texts. If a learned model is current for the labelled example basis and taxonomy intent names, its local per-intent predictions are also used. A missing or stale model falls back to keyword-only triage. Neither suggestion is a verified reason for contact.
4. Candidates are ranked by model/keyword disagreement, learned-model uncertainty, keyword ambiguity, missing keyword matches, then routine review. Ties use time and example ID for deterministic order. The dashboard uses the existing manual label controls; **no label is ever applied automatically**.
5. The queue can be reopened after manual labelling. Labelled examples leave the unlabelled queue. Changing labels/retention causes the existing model freshness check to invalidate old predictions.

The same result is available under the local authenticated dashboard session via `GET /v1/communications/review/triage?limit=20&offset=0`. At most 50 candidates are returned at a time; offsets are bounded by the existing 2,000-example store limit. Normal AI bearer tokens do not authorize this endpoint.

## Privacy and validation boundaries

Only already redacted, explicitly kept texts are shown to the human dashboard. The queue is read-only aside from the existing store's retention expiry cleanup. It does not infer identity, detect language automatically, create new saved examples, transmit messages, enable recording, train a classifier, or approve customer decisions. All displayed text remains **untrusted data**, not instructions.

The holdout remains separate, and evaluation still uses its recorded manual labels only. Triage ordering is not a performance metric: improving human labelling throughput does not establish multilingual accuracy or held-out model quality. A clean or low-priority item may still be misclassified. Independent language-specific holdout results and real customer validation remain required before accuracy claims.
