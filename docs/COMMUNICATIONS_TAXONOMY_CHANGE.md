# R22 — Review keyword taxonomy changes before saving

When refining an intent taxonomy, knowing that more messages match a term is not
enough: false positives and new ambiguity can offset apparent coverage gains.
The local Communications dashboard now supports a paired comparison against the
same already retained, manually labelled training examples.

1. Enable review examples using the existing acknowledgement and retention.
   Label examples from the training population as usual.
2. Open Intent taxonomy and edit terms. Keep the exact existing intent names.
3. Choose **Preview changes on reviewed training messages**. The report shows
   normalized added/removed terms, changed primary decisions (including changed
   tied sets), ambiguity, unclassified volume, per-intent precision, recall,
   F1, accuracy, coverage, positive/negative support, and correctness
   transitions. Select a reviewer-declared language to inspect its aggregate.
4. Review gains and regressions, then choose **Apply reviewed taxonomy**.
   The server checks the current taxonomy, candidate and exact reviewed
   training evidence again before the existing atomic local save. A changed
   label, retained example expiry, consent change or concurrent taxonomy edit
   invalidates the preview. Editing the draft also clears the dashboard pin.
   Existing ordinary Save and Restore default remain available independently.

No model is fitted or altered, no example is relabelled, and no new retention
or collection is enabled. Intent-name changes require a separate label
migration review and are deliberately refused by this preview; ordinary
taxonomy editing behavior is unchanged.

## Semantics

The same existing keyword classifier runs twice. A unique winner is present
and other labels absent; tied top labels abstain, other labels are absent;
unclassified is all absent. Strict correctness requires every declared intent
to be correctly decided. Any decided error makes a message incorrect; otherwise
an abstention prevents correctness. Positive abstentions count in the recall
denominator. Undefined ratios are null, not perfect scores. A multi-intent
review label is not silently projected to one label. A primary result may stay
ambiguous while the set of tied candidates changes: changed_decisions includes
such changes even if the primary-transition diagonal is unchanged.

The selected population is exactly labelled training examples, not a random
sample of customer traffic. Unlabelled and held-out examples are not classified
or used for the preview digest. Report language groups use existing human
labels, not inferred language. The overall aggregate covers every admitted
training row; at most 30 language groups are shown.

Bounded by 2,000 retained examples, 4,000 characters per row, 2 million
training characters, and a conservative phrase-length work estimate. A
budget failure produces no partial report. Unknown/retired labels are
refused. No raw text, example IDs, per-message predictions or terms are
returned by the preview; intent names are user-defined taxonomy labels.
Digests are consistency pins, not authentication or external attestations.
The lock serializes existing local taxonomy writes, reset, review mutations
and preview apply in the local server process. It is not a distributed lock.

This is a development-set tuning aid, not independent validation. A better
training score can mean overfitting. Use an independently frozen holdout
once the taxonomy is finalized and avoid tuning against its results. Do not
claim multilingual accuracy, deployment readiness or verified reasons for
contact from this report.

## Local API

Both endpoints require the existing authenticated local dashboard session,
enabled review examples and JSON. API bearer/MCP clients cannot use them.

- POST /v1/communications/taxonomy/preview: body is the candidate taxonomy.
  Returns aggregate results and preview_sha256; no-store.
- POST /v1/communications/taxonomy/apply-preview: body contains taxonomy,
  preview_sha256 and confirm: true. Requires a current exact basis; a stale
  preview is refused, not silently refreshed. No-store.

The apply path preserves existing ordinary taxonomy write semantics and never
writes to source systems. The preview is read-only except existing expiry
cleanup. No network or provider calls are performed.
