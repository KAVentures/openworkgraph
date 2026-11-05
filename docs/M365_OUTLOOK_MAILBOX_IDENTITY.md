# Live Microsoft Graph mailbox identity preflight

Operator-initiated, **read-only** Microsoft Graph `GET /v1.0/me?$select=id,userPrincipalName,mail` using an access token obtained outside OWG. This is a functional provider request, not a fixture-only workflow. It checks that the token's reported user principal name or primary mail property matches the **separately supplied expected email address**, case-insensitively and exactly. The existing grant's `mailbox_scope` is an opaque label, **not** an email address. The grant must also match the exact tenant label, mailbox label and folder ID.

From the repository root, with an existing owner-only delta grant and owner-only report directory:

```sh
OWG_M365_GRAPH_TOKEN='...' python -m server.m365_outlook_mailbox_identity \
  --tenant TENANT_LABEL --mailbox MAILBOX_LABEL --folder FOLDER_ID \
  --expected-address operator@example.com \
  --grant-file /private/grant.json \
  --output /private/mailbox-identity-first.json \
  --consent-graph-read --consent-private-report
```

The token is never a CLI argument or written to the report. The command requires separate read and private-report consent; validates the current scoped grant before and after the provider request and before publication; uses the existing no-proxy/no-redirect HTTPS transport; limits response size and schema; and creates a new owner-private report without overwriting existing files. The report contains only hashes and matching booleans, not names, email addresses, message contents, access tokens or provider identifiers. Refusals exit 2 without leaking provider details.

**Functional acceptance:** with a real Graph token permitted to read `/me`, an authorized operator can verify the exact expected account identifier and save a private report; mismatches, invalid consent, revoked grants, malformed responses, redirects and unsafe output destinations fail closed. Unit tests exercise transport with a fake opener and do not constitute real-provider or customer acceptance.

**Limitations:** a matching `/me` account property does **not** prove the declared tenant ID, mailbox ownership beyond the token's reported fields, alias authority, historical completeness or permission to read a folder. A `mail` value is provider-reported and may not represent the intended operational mailbox. This is a supplementary preflight, not an import, sync, checkpoint confirmation, append attribution, or retry authorization. Production token lifecycle, tenant verification, real sandbox acceptance and complete incremental import remain outstanding.
