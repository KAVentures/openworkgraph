# Communications intent model: training-basis freshness (R22)

OWG's separately consented communications review stores only redacted examples
chosen by the user, within the 1–90 day local retention. Previously the model
could still be offered for new classifications and its historical holdout
metrics could still be presented after the reviewer changed the very examples
that produced those metrics.

This feature fixes that distinction without changing capture, collection or
sharing permissions. The learned model is now pinned to the exact **labelled
training + held-out review input** and the intent names available when it was
trained.

## Observable product journey

1. Under **Communications → Review examples and learned model**, opt in, add
   examples, label them and train as before. The model status reports
   `usable: true`, `stale: false`, and `evaluation_current: true`.
2. Correct a label or the language for a reviewed example, delete a labelled
   example, or allow a labelled example to expire. The saved model remains
   locally present, but its **quality score becomes historical**:
   `usable: false`, `stale: true`, `evaluation_current: false`.
   The dashboard displays **Learned model needs retraining** and withholds
   the old performance table until retraining.
3. Adding or deleting an **unlabelled** example does not change the basis;
   correcting a label to the exact same value does not either.
4. Rename or remove an intent from the taxonomy and the saved model also
   becomes stale. Editing only a keyword term for an existing intent does
   not affect the independent learned model.
5. A stale model cannot be used for
   `POST /v1/communications/analyze?classifier=learned`: the dashboard
   receives HTTP 409 and a retraining instruction. Keyword analysis remains
   available. Retraining creates a new basis and metrics.
6. Turning off review examples still deletes all kept texts and the trained
   model. Neither the new digest nor the model is exported or uploaded.

## Basis and concurrency

`training_basis_sha256` is SHA-256 over a deterministic local representation
of: intent names; every currently labelled example's exact ID, redacted text,
labels, language, train/holdout assignment and expiry. The digest is stored in
the existing local model file. It is not an identity proof or a substitute for
retention controls; it does not appear as extra raw text in API responses.

- A training run snapshots its inputs, then checks them again **before writing
  the model**. If the reviewer changes the basis during training, no partially
  trained replacement is saved.
- The model-status endpoint checks the latest kept examples and current
  taxonomy intent names. The inference path repeats the check immediately
  before constructing the learned classifier.
- Models saved by earlier OWG versions lack a training-basis digest; they
  fail closed and must be retrained before reuse. The saved historical model
  isn't silently presented as current.
- No new permissions, background jobs, remote calls or message retention are
  introduced.

The holdout remains reviewer-labelled and ID-hash split; a current basis proves
only that the reported metrics refer to the *same* labelled examples, **not**
that language quality, real-world accuracy or independence from the training
population has been established. Additional independent multilingual datasets
are still required for parity.

## Verification

- `tests/test_communications_model_freshness.py` verifies unchanged inputs,
  unlabelled additions, edited training/holdout labels, language changes,
  deletion, expiry, renamed intent lists, legacy files, concurrent training
  edits, and opt-out erasure.
- The existing authenticated HTTP test in
  `tests/test_communications_learning.py` checks a post-training correction
  returns 409 for the learned classifier but leaves keyword analysis working.
- `tests/browser/communications_review.cjs` additionally verifies the
  stale state in the actual dashboard, confirms old F1 scores disappear, and
  repeats WCAG and narrow-screen checks.
