# First Microsoft 365 → OWG operator cycle

The first-cycle command exposes a practical two-step initial import using the **existing production** Graph verification/export and authenticated OWG saved-log append implementations. The steps are **separate invocations** to require a human review of the extracted data and removal markers. No new provider write permission or automatic background sync is introduced.

## 1. Verify and extract (live read-only Microsoft Graph)

Use an existing owner-only private directory, current exact tenant/mailbox/folder grant, and delegated Graph bearer token in the environment (never paste credentials into CLI arguments). Supply the independently expected mailbox address and, preferably, independently expected Entra tenant GUID. Tenant verification requires its separately consented Graph organization read and appropriate permissions.

```sh
python -m server.m365_outlook_first_cycle extract \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --expected-address operator@example.org --expected-tenant-id EXPECTED_ENTRA_GUID \
  --grant-file /private/grant.json --tenant-report /private/tenant-01.json \
  --identity-report /private/mailbox-01.json --folder-report /private/folder-01.json \
  --csv-output /private/first-01.csv --candidate-output /private/first-01.json \
  --consent-graph-read --consent-private-reports \
  --consent-delta-export --consent-tenant-lookup
```

This performs actual bounded Graph GETs and creates a CSV plus SHA-bound candidate only on a complete delta round. The result is `review_required`, **not** an imported log or committed checkpoint. All output paths must be new and owner-private. If tenant verification is omitted, the receipt says `tenant_id_verified=false`; this is not proof of tenant identity.

## 2. Review, then append to authenticated local OWG

Inspect the private CSV and candidate, including any removal markers. Confirm the exact existing OWG saved log and use a current authenticated dashboard session. **Only then** invoke:

```sh
python -m server.m365_outlook_first_cycle append \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file /private/grant.json \
  --candidate /private/first-01.json --csv-path /private/first-01.csv \
  --receipt-file /private/receipt-first-01.json \
  --log-id EXACT_SAVED_LOG_ID --confirm-log-id EXACT_SAVED_LOG_ID \
  --consent-local-read --consent-local-append --confirm-candidate-reviewed
```

The existing append implementation validates the current grant, SHA-bound candidate, exact target and session, then writes a durable attempt latch **before** the authenticated append. A lost response or failed receipt write may mean the append committed: do not retry automatically or remove the attempt latch. Inspect the OWG log and use existing explicit reconciliation/receipt procedures. Only `receipt_persisted` is a confirmed result; the checkpoint remains unadvanced.

For subsequent rounds use the existing `server.m365_outlook_operator_cycle` and explicit resume/handoff/checkpoint tools. Never assert prior import merely because an extraction CSV exists. Removed Graph messages are review markers, not OWG delete commands.

## Acceptance and limitations

- Two independently authorized phases; no combined automatic extract-and-append call.
- First extraction reuses live Graph tenant (optional), mailbox, folder and paginated delta validation; refuses previous cursors.
- Reviewed append reuses authenticated OWG production append and single-attempt durable receipt.
- Missing consent, revoked/mismatched grants, incomplete extraction, unsafe files and uncertain writes fail closed; neither phase advances the checkpoint.
- Focused tests exercise composition and refusal; underlying production reader/append have their own integration tests. **Real Microsoft Graph tenant/customer acceptance and production OAuth lifecycle remain outstanding.** Operator-supplied identifiers are not independent proof of account ownership. This workflow is not continuous autonomous sync, nor proof of individual-event persistence.


## 3. Confirm the append and prepare the next incremental round

After the append returns a **durably persisted** receipt, review the prior
removal markers and independently authorize a fresh local OWG read. Run:

```sh
python -m server.m365_outlook_first_cycle handoff \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file /private/grant.json \
  --candidate /private/first-01.json --csv-path /private/first-01.csv \
  --receipt-file /private/receipt-first-01.json \
  --journal-dir /private/journal --handoff-dir /private/handoff \
  --successor-label second-cycle \
  --log-id EXACT_SAVED_LOG_ID --confirm-log-id EXACT_SAVED_LOG_ID \
  --consent-local-read --confirm-removals-reviewed
```

This invokes the **existing production** resume gate and durable handoff,
including authenticated live OWG readback, scope and candidate digest checks,
and the exclusive private journal/handoff files. It does **not** contact Graph,
commit a provider cursor, delete removed messages or start the next round.
If the append is uncertain, missing, or the current saved-log state no longer
matches, it refuses rather than treating a CSV as proof of import. Existing
identical handoff records can be replayed idempotently; conflicting records
refuse. Both directories must be separate and owner-private.

The operator must separately start the subsequent `server.m365_outlook_operator_cycle
extract` command, using this first candidate/CSV/receipt, the journal and
handoff directories, the **same successor label**, and new output paths.
Subsequent reads remain consent-gated, bounded and explicitly reviewed.
This is not unattended sync or proof of real customer-provider acceptance.
