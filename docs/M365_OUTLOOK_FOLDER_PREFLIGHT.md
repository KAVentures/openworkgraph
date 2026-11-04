# Microsoft Graph exact-folder preflight (operator initiated)

This is a **live, read-only** Graph metadata GET for the exact folder chosen by an operator, not a synthetic connector fixture. It reuses the existing private Microsoft 365 folder grant, disables redirects/proxies through the existing transport, requires two separate consents, and produces a new owner-private report. It does not read message bodies, subjects, send mail, append events, import logs, advance cursors or retry uncertain writes.

From the repository root, prepare an existing current owner-only delta grant and an owner-only output directory. Supply a Graph access token only through the process environment (never CLI arguments or report files):

```sh
OWG_M365_GRAPH_TOKEN='...' python -m server.m365_outlook_folder_preflight \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file /private/grant.json \
  --output /private/folder-preflight-first.json \
  --consent-graph-read --consent-private-report
```

The CLI calls `GET https://graph.microsoft.com/v1.0/me/mailFolders/{id}` with a four-field `$select`, verifies the returned exact folder ID and bounded counts, then rechecks the grant before creating a new report. A refusal returns exit code 2 without printing provider identifiers. The report contains only scope/folder/response digests, folder counts and explicit limitations. Existing report files are never overwritten.

**Acceptance:** with a real operator-authorized Graph token and matching grant, the exact folder metadata can be observed and recorded without any Graph mutation or OWG write; invalid consent, revoked/mismatched grant, wrong folder ID, redirects, malformed responses, or existing destinations must fail closed. Unit tests use a fake transport and are **not** live Microsoft Graph or customer acceptance.

**Limitations:** `/me` refers to the bearer token's identity. A successful folder GET does not independently verify the declared tenant/mailbox, certify the provider deployment, establish historical completeness, or authorize checkpoint movement or recovery. Use only an access token independently obtained for the intended account; verify tenant/mailbox externally before using the output in operational decisions. Production token rotation and customer sandbox acceptance remain separate.
