# Decision coverage lab (R21)

The local process catalog offers a **Decision coverage lab** for a saved design
containing decision tables. This supplements supplied-case checks with exact
enumeration of a deliberately finite synthetic input domain.

## Review and run

1. Open the saved process revision and expand the lab.
2. Choose an explicit calendar date. Effective-date and confirmation checks
   use the existing decision evaluator.
3. Select **Suggest synthetic boundary choices**, or enter your own dimensions.
   Suggestions use only declared predicates in the selected design: expected
   literals, adjacent representable numeric values, a missing marker, null,
   booleans and a generic string/number. They are editable examples, not an
   exhaustive partition of the possible facts. Omitted and clipped paths are
   disclosed. Narrow the domain if its Cartesian product is too large.
4. Review the choices and select **Check finite decision domain**. Use synthetic
   values only, not actual customer or employee data. No source connector or AI
   model is contacted.
5. Inspect per-table outcomes, matched/selected row counts, co-matching row
   pairs, FIRST shadowing and example choice indices. Download the minimized
   JSON report explicitly if desired.

Example request:

```json
{
  "as_of_date": "2026-11-24",
  "dimensions": [
    {"path": "/amount", "choices": [
      {"value": 99}, {"value": 100}, {"value": 101},
      {"missing": true}, {"value": null}
    ]},
    {"path": "/approved", "choices": [
      {"value": true}, {"value": false}
    ]}
  ]
}
```

This example tests all ten combinations, without assigning probabilities.
Each choice is a scalar or an explicit missing marker. Missing differs from
null, and booleans differ from numbers. Equivalent numeric choices such as
1 and 1.0 are refused as duplicates. Dimensions construct nested JSON objects,
not arrays. Overlapping ancestor/descendant dimensions are refused. Paths
not explicitly supplied or injected by a direct dependency are listed.
The existing evaluator handles dependencies in order; a missing upstream
selection or conflicting supplied dependency fact is never guessed.

## Interpret the result

- A UNIQUE overlap means multiple rows matched in a tested combination.
- FIRST can have co-matching rows; an earlier established selection shadows
  later matching rows. An unknown earlier row may instead block selection.
- A gap means every row was known false for that combination.
- Unknown, expired, unconfirmed and dependency-blocked cases are distinct
  results and never counted as proven unreachable rows.
- A row known never to match requires evaluation of every combination with
  no unknowns. Fully shadowed requires at least one match and every match
  shadowed by an earlier selection, with no unknown or unevaluated cases.
- Counts describe a finite, caller-reviewed product, not a distribution of
  business cases, statistical coverage or legal compliance.

The report includes exact process/revision/content pins and a SHA-256 digest
of the normalized JSON request. Example vectors are zero-based choice indices
in the original dimension order. Keep the synthetic request independently
if you need to reproduce the cases. Raw input values are not included in
the report or persisted by the lab. The digest is not an anonymity guarantee.
Changing a date, input, draft or selected revision invalidates the current
report and download control.

## REST and resource bounds

Both endpoints require the authenticated local dashboard session and exact
workflow ID, revision and content digest:

- `GET /v1/process-catalog/decision-domain/suggestions?workflow_id=...&revision=...&content_sha256=...`
- `POST /v1/process-catalog/decision-domain?workflow_id=...&revision=...&content_sha256=...`

POST accepts at most 64 KiB, up to eight dimensions, 24 choices per
dimension and 2,048 total combinations. Oversized domains are refused
before enumeration, never silently sampled. Invalid JSON, duplicate keys,
unknown fields and nonfinite numbers are refused. Existing model, fact and
predicate bounds apply. Each outcome category and row example category
contains at most six indices; every co-matching pair has one witness.
Counts are exact for the submitted finite domain. Requests and reports are
not persisted and no organization sharing is introduced. Responses are
no-store. A stale content digest is a 409; a missing revision is a 404.
The normal API bearer is insufficient.

The synthetic fixtures cover threshold overlap, FIRST shadowing, gaps,
unknowns, dependencies, date/confirmation boundaries, malformed and oversized
domains, minimized reproducible output and local HTTP authorization. This
feature neither proves general DMN/FEEL coverage nor authorizes agent action.
