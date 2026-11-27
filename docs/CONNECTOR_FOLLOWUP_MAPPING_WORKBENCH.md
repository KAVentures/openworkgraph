# Guided follow-up reassessment mapping workbench

Refs #1572, #1631 and #370. The completed-follow-up review console now accepts the two existing exported reviewed request files and guides the operator through source, linked case and activity correspondence without hand-editing a comparison JSON file. The advanced JSON input remains available.

## Operator workflow

1. Export the exact baseline conformance request from the original case review and retain it privately. Once later evidence is available, review distinct later publications and export the later linked-case request. Both sets of publication grants must still be live.
2. Open the completed-unverified follow-up packet and its guided mapping workbench. Select the two request files (up to 1 MiB each), explicitly approve inspection and load eligible mapping candidates. The existing gateway request-size cap also applies.
3. Choose one distinct later source for each original source. Candidates match provider, disclosure mode, and post-completion completed request-window constraints. A compatible source is not proof of equivalent scope or business identity.
4. Choose one distinct later case per original packet case. The UI filters by mapped source membership multiplicity and evidence-basis category. The server independently rechecks both.
5. For each distinct selector, review its later corresponding activity. Labeled activities must match exactly. Coded activities can be selected from a bounded observed-activity catalog (up to 200 distinct activities per later source, sorted by activity) or entered explicitly when not listed, including an independently reviewed absent code. Catalog truncation is disclosed. The system never infers semantic equivalence of codes.
6. Approve the exact correspondence, preview the later evidence assessment, and separately approve recording and downloading the report. The existing reassessment endpoint recomputes both evidence samples, enforces the original rule semantics and all revision/grant fences, and records only digest receipts. Editing the mappings clears the preview and approval.

The guidance endpoint accepts the original Review and later Plan plus exact linkage digest, verifies the baseline against the original queue, computes the later review, and exposes only the necessary job and linked-group metadata, original selected rules and later observed activity counts. No source event IDs, raw case membership, source rows or credentials are returned. Rules and activity labels/codes can be sensitive; guidance requires explicit owner inspection approval and is not stored as a separate document. Inspection and recomputation generate existing counts-only audit events. Neither source reads nor writes are performed.

No automatically selected mappings, independently verified business identity, actual corrective action or source completeness are claimed. The original follow-up status and exception outcome are unchanged. Existing publication coexistence constraints remain; this workflow does not support replacing the original grant with a later publication on the same job.

## API

Owner-only POST /v1/admin/connector-events/{organization_id}/exception-queues/{queue_id}/followups/{packet_id}/mapping-guidance. Accepts expected_queue_revision, expected_packet_revision, baseline, subsequent, confirm_mapping_inspection. The subsequent input is the reviewed linkage Plan plus expected_review_sha256, without conformance rules. Output includes a guidance digest, eligible source candidates, group summaries, selected baseline rules and bounded activity catalogs. This is inspection, not authorization; submitted mappings are independently checked by the existing reassessment-preview and reassessment-record operations.

## Verification

Regression fixtures exercise the actual Gateway publication-store guidance and subsequent comparison, candidate timing, exact baseline and later review pins, minimization, consent, revision conflicts and revocation. Browser acceptance verifies the guided form appears on a completed packet and cannot inspect without both files. The browser fixture does not yet execute the full later-source mapping path; backend fixtures cover this path. Modified JavaScript sources pass syntax checks. Full runtime CI and real provider acceptance remain outstanding.
