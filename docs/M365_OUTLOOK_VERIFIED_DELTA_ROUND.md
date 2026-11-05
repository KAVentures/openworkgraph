# Verified Microsoft Graph delta export (operator-controlled)

This command combines the existing **live** Graph `/me` mailbox identity read, exact folder metadata read and bounded paginated folder delta extraction in that order. It is a functional read-only provider workflow, not an automatic background connector. All three stages reuse the same operator-provided bearer token, current exact tenant/mailbox/folder grant and existing strict HTTPS transport. It produces private identity and folder reports, an event-log CSV, and a candidate delta cursor.

Create an owner-only private directory and a current, matching grant as documented in `docs/M365_OUTLOOK_MAILBOX_IDENTITY.md` and `server/m365_outlook_delta.py`. Supply a delegated Graph token externally, with only the Graph scopes required by the underlying operations. Do not paste tokens into commands, files, shell history or tickets.

```sh
export OWG_M365_GRAPH_TOKEN='REDACTED_TOKEN_FROM_SECURE_ENV'
python -m server.m365_outlook_verified_delta_round \
  --tenant TENANT_LABEL --mailbox MAILBOX_LABEL --folder FOLDER_ID \
  --expected-address operator@example.org \
  --grant-file /private/grant.json \
  --identity-report /private/mailbox-identity-001.json \
  --folder-report /private/folder-preflight-001.json \
  --csv-output /private/delta-001.csv \
  --candidate-output /private/delta-001.json \
  --consent-graph-read --consent-private-reports --consent-delta-export
```

For a later round, pass `--previous-candidate /private/delta-001.json --confirm-previous-import` **only after independently confirming** that the previous CSV was imported completely into the intended OWG saved log. The flag is an operator attestation, not proof of import. A failed round must not be resumed by asserting a successful import that did not occur. Each output path must be new; no overwrites.

**Acceptance:** the command refuses missing consent, mismatched/revoked scope, unexpected Graph identity, missing folder, unsafe responses, incomplete pagination or duplicate/unsafe output paths. It performs the mailbox and folder reads before any delta request, rechecks the grant between stages and relies on each underlying reader to recheck it during its provider operations. It does not import the CSV, commit the cursor, retry writes, delete records, or infer mailbox tenant ownership. Removed source records appear only as review markers.

**Failure and partial output:** a later stage can fail after earlier reports or the CSV have already been created. Treat every such artifact as incomplete review evidence; do not use it as a checkpoint or retry authorization. Reports and CSV contain minimized metadata, not message bodies. The returned status does not certify tenant ID, provider credential lifecycle, real customer acceptance, or complete historical coverage.

Focused tests verify orchestration and refusal with substituted provider functions. **Real Graph sandbox acceptance, authenticated OWG import confirmation, revocation/retention integration and production OAuth lifecycle remain outstanding.**
