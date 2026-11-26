# Communications model holdout overlap audit (R22)

A reviewer-labelled holdout chosen by example ID can contain messages using the
same template as training examples. A high raw F1 score can therefore be
misleading. This addition reports both the ordinary holdout and a second
evaluation restricted to messages without detected lexical overlap with
labelled training examples. It does not change example assignments, training,
model predictions, retention, or the default keyword analysis.

## Review in the dashboard

Train or retrain a local intent model using the existing consented review
workflow. The model panel displays:

- The original held-out score, with all labelled holdout examples.
- Normalized exact matches and near lexical matches against labelled training
  examples, as disjoint counts, including per-language counts.
- The remaining sample's macro F1, per-intent precision, recall, support and
  language-specific F1. Existing minimum-support rules still apply.
- Optional language gate results for the remaining sample, rather than the
  original sample, for newly trained models.

The optional gate refuses classification when screened evidence is inadequate,
including incomplete overlap audits. Normal learned mode remains unchanged.
Older models retain their original gate semantics and visibly disclose that
the evaluation was unscreened; retraining enables the new behavior.

## Method and limitations

The local audit uses NFKC case-folded word tokens, with numeric runs normalized
and punctuation ignored. Exact token sequences match even across reviewer
language labels. Other texts with at least five distinct word trigrams are
compared using set Jaccard similarity of at least 0.8. Exact matches take
precedence; each holdout row is counted once. The audit compares only labelled
examples and deduplicates training signatures for comparison. A fixed
20-million intersection-work budget and existing 2,000-example / 4,000-character
bounds cap its cost. If the audit cannot complete, it returns no screened
score rather than passing unchecked examples.

This heuristic can flag legitimate similar messages and miss paraphrases,
translations, shared threads, shorter near-duplicates and subtle leakage.
The remaining sample is not a random or independent test population; screening
may change the language/intent distribution. Do not interpret a higher or
lower screened score as a causal improvement, or claim externally validated
model quality. Use independently collected, thread-disjoint, multilingual
holdouts before production quality claims.

Only aggregate counts and ordinary quality metrics are persisted and returned;
no message texts, signatures, matched IDs or source credentials are added to
the report. Existing opt-in review retention, deletion, and model freshness
rules apply to both scores. No source access, external transmission or action
authority is introduced.

## Regression coverage

Run `pytest tests/test_communications_holdout_overlap.py tests/test_communications_learning.py tests/test_communications_quality_gate.py`.
The existing real-browser communications review acceptance additionally checks
the new tables and gate basis. Full CI and real independent holdout validation
remain necessary.

Related: [#370 R22](https://github.com/KAVentures/openworkgraph/issues/370).
