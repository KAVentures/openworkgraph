# Microsoft 365 delta candidate review (offline)

After a separately consented Microsoft Graph delta export, run:

```sh
python -m server.m365_outlook_candidate_review \
  --tenant TENANT --mailbox MAILBOX --folder FOLDER_ID \
  --grant-file /private/grant.json --candidate /private/candidate.json \
  --consent-local-read
```

The command checks the current exact tenant/mailbox/folder grant, owner-private artifacts, production candidate schema and complete delta cursor, SHA-256-bound CSV, bounded event rows, event identities and removal-marker count. It returns only a minimized summary and digest, not message IDs, cursor URLs, tokens, email content or event-level data. A revoked grant, altered CSV, changed scope, missing consent or unsafe artifact fails closed.

**Operator action:** inspect the private CSV and candidate removal markers, verify the intended saved-log destination, and separately invoke the existing consented append command. The summary is not an append authorization, event-level persistence proof, real-provider acceptance or provider checkpoint commit. No Graph or OWG network request is made by this command; no automatic retry or background synchronization occurs.

Acceptance still requires real Microsoft Graph tenant/customer testing, authenticated OWG append/readback and all repository CI gates.
