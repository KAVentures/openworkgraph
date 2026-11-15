# Optional language-specific holdout quality gate (R22)

OpenWorkGraph already supports learned, multi-label intent classification from
separately consented, locally stored and redacted review examples, including
a never-trained-on holdout split. A fresh model can be weak on one language
while its overall F1 appears strong. Historical metrics alone do not prove
that a classifier is reliable for a given new export.

This feature adds an **optional, fail-closed holdout quality gate**. It does
**not** change the default keyword or learned classification workflows.

## Analyst journey

1. Opt in to review-example retention, label messages with intent(s) and
   language codes, and train as before. OWG shows per-intent and per-language
   held-out quality scores.
2. The model panel now lists a readiness result for each language represented
   in the labelled holdout. It also discloses that these are **reviewer-labelled
   sample metrics, not externally verified accuracy**.
3. When analysing an export, choose **Learned model** and optionally enable
   **Require a strong reviewer-labelled holdout for this declared language**.
   Enter the language of the entire export, e.g. `sv`, `en` or `pt-BR`.
4. Before the export is read or temporarily stored for processing, OWG checks
   every taxonomy intent against the model's current holdout for that language.
   If any check fails, it refuses the request (HTTP 409), telling the analyst
   to review holdout support/quality, not silently switching to keyword
   classification or inventing confidence.
5. Leave strict mode off to retain the existing learned-classifier behavior.
   A stale or expired model remains unavailable regardless of the gate.

## Predeclared descriptive thresholds

For *every* taxonomy intent in the selected language:

- at least **5 positive** and **5 negative** labelled holdout cases;
- holdout **F1 ≥ 0.80**;
- **uncertain classifications ≤ 35%**.

An intent absent from the trained model, a language absent from the holdout,
an unavailable score, or too-small positive/negative support blocks the
strict request. These are review thresholds, **not** statistically certified
risk guarantees. Language selection is a declaration by the analyst:
OWG does not automatically detect or verify the actual language or languages
inside an import. A mixed-language export does **not** become validated by
choosing one code. The holdout is randomly split by example ID and may still
share source patterns with training examples; external representative tests
are required before claiming market-ready multilingual quality.

A model's reported F1, recall, precision and expected calibration error
remain descriptive; this gate does not change or retrain its coefficients,
infer the truth of an intent or authorize an action.

## API

The existing local-dashboard-session-only
`POST /v1/communications/analyze?classifier=learned` now accepts optional
`quality_gate_language=sv`. The parameter is invalid for keyword mode;
unsupported language input produces a safe validation error. Language
quality checks run before reading the uploaded export.

`GET /v1/communications/review/model` adds
`holdout_language_gates`: only for a fresh model, keyed by an evaluated
language. Each entry states `ready`, bounded per-intent reason codes,
support criteria and explicit `independent_external_quality_verified=false`.
The `model_reference` on a successful strict analysis records its
declared-language gate, model digest and
`source_language_independently_verified=false`.

The feature introduces **no new retention, raw-text exports, source
connections, remote API calls or capture permissions**. No incoming
messages can change the test thresholds or issue new instructions.

## Validation

- `tests/test_communications_quality_gate.py`: missing modelled intents,
  language absence, low positive support, weak F1, high abstention,
  malformed language, direct classifier use and real authenticated
  request rejection before upload processing.
- `tests/browser/communications_mining.cjs`: strict controls appear only
  in learned mode, remain off by default, and don't change keyword flow.
- The normal communications review, model-staleness and browser acceptance
  suites should continue unchanged.
