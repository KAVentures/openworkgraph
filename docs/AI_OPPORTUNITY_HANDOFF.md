# External AI proposal to implementation portfolio (P0 #1663)

This feature builds on the portable AI implementation review in #1667.
It does not add another proposal format, inference model or opportunity
database. It connects a customer's external AI proposal to the existing
automation opportunity pipeline.

## Workflow

1. Export the original workflow evidence from OWG. In the local dashboard's
   Automation pipeline, open **Add an external AI proposal to the implementation
   portfolio**, select and order the original JSON files, choose a context byte
   budget, and select **Prepare evidence packet**. Review the event/gap counts,
   byte size and exact SHA-256. Download the exact canonical packet and fixed AI
   instructions separately. The existing `shared.ai_implementation_review` CLI
   remains an alternative. The budget is bytes, not provider tokens; oversized
   evidence is refused, not silently truncated. The local dashboard API uses
   the same 8 MiB request envelope cap. Changing source order/files or budget
   invalidates the prepared download. No automatic upload or model invocation
   occurs; inspect the contents and use only a company-authorized external AI.
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

## Optional loopback model route

After preparing a packet of at most 512 KiB, expand **Optional: generate a
draft with your local model**. Enter the exact model ID and configured service
port, choose the output-token bound and optionally JSON-object mode. Confirm
sending the selected evidence to that service. The owner-only endpoint
rebuilds the packet and checks its exact digest before making one request to
the OWG server's IPv4 loopback. A process-wide single-flight slot refuses
concurrent local inference; no retries, tools, credentials or cloud fallback
are introduced. The local service can still proxy or log data; verify its
configuration and company authorization. A socket timeout is not a guaranteed
wall-clock deadline. Revoking a session can suppress the result but cannot
undo a request already sent.

Inspect the returned proposal as untrusted text; choose **Use generated
proposal for review** to use it in the existing human-reviewed opportunity
flow, or download the exact proposal and usage receipt. The receipt reports
endpoint-reported token counts, elapsed time and digests, not independently
verified cost or location. A missing count remains unknown. Changing source
files, order, budget or model settings discards the generated draft and
requires fresh confirmation. The manual external AI file import remains
available. No idea is created by generation, and all existing review and
confirmation gates still apply.

## Compact evidence for manually connected external AI

After preparing a packet of at most 512 KiB, the dashboard also offers
**Download compact evidence packet** and **Download compact AI instructions**
when the exact combined evidence and guide byte count is smaller than the
original pair. Inspect both matching files locally before deliberately
providing them to an authorized Claude, ChatGPT or other model. The original
download remains available. The compact attachment preserves the original
occurrence references and packet digest; its matching guide requires the
model to cite the original occurrences and return the original packet pin.
The server verifies exact canonical expansion before offering the download.
File order or budget edits invalidate both compact downloads. No uploads,
provider calls, permission changes or deployment occur. Compact evidence
is not redaction and smaller byte counts do not guarantee lower provider
token consumption or equivalent reasoning quality.

## Optional compact evidence transport

For prepared packets with a selected budget of at most 512 KiB, OWG reports
the exact original and compact evidence byte sizes. The local model defaults to
the original format. Choose **Lossless compact evidence (experimental)** only
when the preview saves bytes, then explicitly confirm another request. OWG
reconstructs the exact original canonical packet before sending the compact
representation. The model receives resolution instructions and must cite the
original scoped occurrence references and packet digest; all proposals still
undergo the same review against original source evidence. No automatic fallback
or additional model request occurs. Changing the representation clears the prior
draft and confirmation.

Download the receipt for each representation when comparing them on the same
source, model and settings. It includes original/transmitted context bytes,
actual request/response sizes, endpoint-reported tokens, latency, context digest,
output-token bound and JSON mode. Byte savings are not token savings, and neither
structural citation validation nor equal evidence establishes equal model quality.
Use independent held-out assessment before selecting compact mode in production.
The original packet and cloud/manual handoff remain unchanged.

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

## Directly select observed work without exporting a file first

Expand **Select observed work directly from OWG**. Enter an explicit offset-aware window of at most seven days. The local candidate search uses the existing workflow-evidence family navigation endpoint, with 25,000 source rows, at most 20 families and at most 12 examples per family. Derived families are not verified workflows, ranking is not task confidence, and the bounded population does not establish coverage. Select individual observed execution IDs (or paste exact IDs directly, including a known run not shown in the navigation list). Then explicitly replace the current handoff sources with the requested canonical evidence. The picker requests at most 12 selected runs with up to 100 returned events per run and refuses mismatched or omitted IDs/windows or responses exceeding 2 MiB. Visible coverage flags include the source limit, truncated or empty executions and unverified event membership. These are not completeness scores.

The original local API response is retained as a browser-memory file without JSON reserialization. Download it if needed for later exact proposal verification. The existing preparation, external-AI instructions, optional local inference, trials, opportunity review and native skill draft paths use that same source; no new model, data store, authorization, background capture or upload is introduced. Existing file import stays available. Changing the picker draft alone does not replace the current handoff source; a successful explicit selection clears old packet/proposal-review and inference confirmation state. A refused selection leaves previous sources intact. Review authorization, sensitive content and evidence coverage before any separate external AI request.
