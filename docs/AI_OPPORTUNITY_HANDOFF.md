# External AI proposal to implementation portfolio (P0 #1663)

This feature builds on the portable AI implementation review in #1667.
It does not add another proposal format, inference model or opportunity
database. It connects a customer's external AI proposal to the existing
automation opportunity pipeline.

## Workflow

1. Export the original workflow evidence from OWG and prepare the portable
   packet with the existing `shared.ai_implementation_review` command.
2. Have the external AI return a proposal using that packet's exact
   reference and proposal format. Review the proposal and material business
   assumptions yourself.
3. In the local dashboard, open Evidence / Automation pipeline, then
   **Add an external AI proposal to the implementation portfolio**.
   Select the original evidence JSON files in their original packet order
   (use the reorder controls if necessary) and the returned proposal JSON.
   Enter a human-reviewed opportunity title and description.
4. Choose **Review exact proposal**. The authenticated local server
   recomputes the original structural review; it does not trust a supplied
   review receipt. The preview includes cited event counts, unresolved
   questions, coverage gaps, candidate environments and exact digests.
5. Review the original evidence and proposal, acknowledge the confirmation
   checkbox and choose **Create unassessed idea**. The server recomputes
   the review and requires the same preview digest. Changes to files, their
   order, title or description require a fresh preview.
6. The ordinary opportunity pipeline now contains the new idea. Use its
   existing assessment, ownership, budget, dependency and approval controls.
   The idea is initially unassessed and has no process-catalog binding,
   execution grant or verified savings.

The existing opportunity store retains only the title, description, manual
source reference and structural lineage (packet, proposal, review and
preview digests). Raw evidence files and proposal content are not retained
by this feature; preserve the originals separately under customer policy.
The source reference is not live source verification or a verified process
revision. The structural lineage is not a signature, human identity,
proof of correct interpretation or proof of authorization.

Only local dashboard sessions can preview or create an idea. A read-capable
MCP/API bearer does not grant that privilege. The request is bounded to
8 MiB including the JSON envelope; one to eight original evidence files
are supported. Duplicate JSON keys are refused in the original selected
documents. Repeated confirmed imports of the same packet/proposal are
refused while the original idea exists, using the existing pipeline's
SQLite transaction. Deleting the idea permits a new import. No model is
called, no evidence is uploaded to an external provider, no existing
pipeline assessment or state transition is bypassed.

## Verification

Focused pure handoff tests cover exact pinning, stale previews,
human-entered text, strict confirmation and refusal of fabricated approval
flags. Repository integration tests exercise the actual portable review,
concurrent duplicate creation, local dashboard auth and persisted lineage.
Chromium regression coverage exercises the real dashboard import, stale
preview invalidation and existing pipeline refresh. Full repository and
packaged-client acceptance remain required before release.

```sh
python -m pytest -q tests/test_ai_opportunity_handoff.py tests/test_ai_opportunity_integration.py
```
