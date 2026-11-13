# R11 clarification decisions: source-bound review

## Why this gate exists

The local process-design clarification helper can compare an explicitly supplied
policy/SOP document with a selected saved process revision. Previously its
decision ledger used a question ID and process revision only. If a document
changed while the process revision stayed the same, an old answer could still
hide a newly relevant contradiction or rule question.

Clarification decisions are now bound to **both** the existing exact process
revision/content digest and a SHA-256 fingerprint of the **complete ordered
source-document set**, including the exact Unicode title and text of each
document. No original document text is saved for the purpose of fingerprinting.
The existing decision ledger still retains the cited excerpts the reviewer
explicitly chose and the human's response.

A changed title, text, document count or ordering changes the fingerprint.
This conservative policy reopens every question whose recorded source set
no longer matches, even if some statements did not change. That extra review
is intentional: unrelated assertions are not silently assumed to be current.

## API and UI

1. `POST /v1/process-catalog/clarify?workflow_id=...&revision=...`
   returns `documents_sha256` alongside the currently open questions.
   Matching previously answered questions are hidden **only** for the exact
   process revision and that exact document-source digest.
2. The user reviews questions and cited statements.
3. `POST /v1/process-catalog/clarify/decide?workflow_id=...&revision=...`
   requires `decision.expected_documents_sha256` equal to the hash of the
   `documents` in the same request. A changed document or missing pin yields
   HTTP 409, requiring the reviewer to reload.
4. Saved ledger records include `documents_sha256`. The history endpoint
   preserves prior decisions for earlier document source sets; these remain
   inspectable but do not suppress future review questions.

The local dashboard includes the pin in its decision request. A person cannot
replay a previously loaded question after editing SOP content and accidentally
apply it to a new source set. The endpoint still never changes permissions,
automatically updates the business rule or authorizes process execution.

## Compatibility and limitations

Legacy decision records without `documents_sha256` remain historical only.
They **do not** count as reviewed for newly requested source content. Clients
of the `/clarify/decide` endpoint must now include the source pin returned by
`/clarify`. This is a deliberate fail-closed API contract adjustment.

Source hashes show equality of caller-supplied text, **not** authenticity,
external revision control, legal validity or a verified business-policy source.
The implementation is local-only, not an enterprise-wide policy authority.
A future authorized provider connector may independently attest the origin
and freshness of documents before treating their claims as current.
