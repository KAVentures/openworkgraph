# R11 — Compare policy source revisions

Open a saved process revision, supply the current SOP or policy documents
in its clarification section, then open **Compare previous and current
policy source bundles**. Paste or load the previous exact bundle as a JSON
array of objects with title and text. Compare the same process revision
against both explicit bundles. No text is saved by the comparison.

The comparison uses exact trimmed indexed statements as a multiset. An
identical statement appearing twice is counted twice. A wording change is
one removal and one addition, not an inferred semantic replacement. Stable
occurrence pairing does not establish document lineage. The separate
bundle digests also detect source titles, order and otherwise unindexed
text changes. Exact character ranges and original source indices are
available in paginated evidence. The indexed statement and conflict scan
limits match the existing clarification engine; incomplete coverage is
reported rather than treated as complete.

Every declared business rule, activity/decision and terminal outcome is
reviewed with the existing lexical matching and bounded conflict scan.
Flags show added/removed matching occurrences, missing matches, possible
conflicts on either side and incomplete scans. Raw instruction-like text
can appear in the exact comparison but never contributes to lexical
subject matching. No lexical match or absence of a flag proves semantic
equivalence, policy authority or consistency. Human decisions remain
bound to the original revision and exact source bundle. Use the existing
clarification workflow to record candidate-bundle decisions.

The local-dashboard-only no-store POST endpoint
/v1/process-catalog/clarify/source-revision-review takes workflow_id and
revision query parameters; expected_content_sha256 and baseline/candidate
document arrays in the body. Optional change_kind, subject_type, target_id,
offset, limit and expected_review_sha256 support pinned evidence pages.
Both bundles are required again for each page. At most five documents
per bundle, 60,000 characters per document, 1,500 indexed statements per
bundle, 50 evidence occurrences per page, 800 KiB request. The report
does not write to the ledger or change any model or execution grant.
JSON download includes the current evidence page and subject summary.
Tests exercise exact counts, duplicate occurrences, offsets, source
reorder, instruction-like statements, stale pins, missing coverage,
strict inputs, authorization and ledger isolation. Native/browser CI
and real policy accuracy validation remain required.
