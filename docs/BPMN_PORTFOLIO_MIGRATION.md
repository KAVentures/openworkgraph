# Assess a BPMN portfolio before migration

Organizations moving existing process designs into OpenWorkGraph can first
assess a batch of explicitly selected BPMN 2.0 files without opening every
diagram in the catalog. This offline CLI uses the **same importer** as the
authenticated catalog preview rather than a second, approximate parser.

From a source checkout with OWG's Python dependencies installed:

    python -m shared.process_bpmn_portfolio procurement.bpmn invoicing.bpmn
    python -m shared.process_bpmn_portfolio --format csv procurement.bpmn invoicing.bpmn > worklist.csv
    python -m shared.process_bpmn_portfolio --fail-on-remediation procurement.bpmn

Pass only files you are authorized to inspect. Input order is the file ordinal
in the report. Keep the command's input list locally if you need to associate
the ordinal with a file name. The report records a SHA-256 of each fully read,
bounded document, and process ordinals follow the inspection order of that
exact source. It does not retain original file paths, process or activity names,
source identifiers, documentation, rules, or XML. Digests can still correlate
identical documents and counts disclose structural characteristics; treat the
report as potentially confidential. It is not uploaded or published by OWG.
Shell redirection creates a user-controlled local file; use a new filename to
avoid replacing an existing report.

## Reading the worklist

- **draft_review_required:** the actual importer created a valid unreviewed
  draft. It always needs normal human review, even if there are no additional
  remediation indicators.
- **draft_with_remediation:** the draft includes extra review work, such as
  placeholder labels, documentation, manual guards, lowered constructs,
  inlined subprocesses, message-flow notes, source executable flags or typed
  task implementation semantics which OWG does not import.
- **refused:** the document or selected process is outside the current
  importer's supported contract. A fixed diagnostic explains the refusal.
  A multi-process collaboration, for example, cannot be treated as independent
  designs. A refusal is not an estimate of the original process's quality.
- **not_assessed:** the source could not be read or an explicit assessment
  budget was reached. Do not treat it as an import success or refusal.

The canonical JSON includes file and process status counts, known process
population, files whose process population is unknown, attempted previews,
warning and refusal counts, and aggregated review workload for successful
drafts only. The CSV presents one row per assessed process or document-level
refusal/nonassessment, with blank counts when no draft exists. Counts of
warnings refer to drafts, not occurrences inside a model. Some importer
diagnostic lists are themselves bounded (for example lowered constructs);
counts describe the returned importer preview, not an independent census of
every source XML construct.

The batch is limited to 100 explicit files, 128 KiB each, 40 selectable or
blocked processes per file and 500 attempted import previews per batch.
If a file would exceed the remaining process preview budget, none of its
processes are partially previewed. Known skipped populations are reported,
and subsequent smaller files can still be assessed. Split a large batch and
repeat. Non-regular files are not read. No directory scans, archives,
provider calls, XML resolution, catalog saves, approval propagation or
execution are performed.

Exit code 0 means every selected source can become an unreviewed draft;
1 means a refusal or nonassessment occurred (or additional remediation with
`--fail-on-remediation`); argparse uses 2 for invalid command usage.
Unexpected implementation errors fail the command rather than being
misclassified as invalid business models.

## Migrate a selected design

Use the normal [catalog BPMN import](PROCESS_BPMN_IMPORT.md) and inspect the
exact document again. Compare the source digest and process ordinal from the
portfolio report, then select the independent process if applicable. The
catalog's existing digest pin protects the preview from a changed file.
Resolve labels, unsupported constructs and documentation in the original
model or reviewed draft before saving. Review business rules and tool
mappings separately. The assessment is **not** permission to execute, proof
of semantic equivalence, a conformance score, a competitor certification,
or evidence of actual observed work.

This supports the R13 modeling and migration gap in issue #370 and the
broader workflow mapping and process repository goals in #264 and #326.
Real customer model inventories and independent external modeling-tool
acceptance remain separate from the synthetic tests.
