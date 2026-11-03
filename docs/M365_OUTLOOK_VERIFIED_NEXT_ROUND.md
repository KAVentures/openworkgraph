# Verified operator-controlled Microsoft Graph next delta round

This command is the first **real provider-read** integration of the existing OWG verified import receipt, live resume authorization, private decision journal, and durable handoff. Unlike the plain delta CLI's manual `--confirm-previous-import` assertion, this entrypoint **requires an existing handoff** and revalidates it against the current authenticated local OWG event inventory before it sends any Microsoft Graph GET.

Prerequisites: complete prior delta export; explicit OWG append and count receipt; separately reviewed removal markers; private operator decision journal and handoff (see `M365_OUTLOOK_RESUME_HANDOFF.md`); current active scoped grant; authorized Graph delegated bearer in `OWG_M365_GRAPH_TOKEN`; current local dashboard session in `OWG_DASHBOARD_SESSION`. All artifact directories must be private; the new output and candidate must not exist. Run from the repository root:

```sh
python -m server.m365_outlook_verified_next_round \
  --tenant reviewed-tenant --mailbox reviewed-mailbox --folder AQMkSelectedFolder \
  --log-id log_0123456789abcdef --confirm-log-id log_0123456789abcdef \
  --grant-file /private/owg/grant.json \
  --previous-candidate /private/owg/round-001.json \
  --previous-csv /private/owg/round-001.csv \
  --receipt-file /private/owg/round-001-receipt.json \
  --journal-dir /private/owg/resume-journal \
  --handoff-dir /private/owg/resume-handoffs \
  --successor-label round-002 \
  --output /private/owg/round-002.csv \
  --candidate /private/owg/round-002.json \
  --consent-local-read --confirm-removals-reviewed \
  --consent-delta-read --consent-local-export
```

The wrapper checks the already persisted handoff identity from the prior CSV digest and OWG log ID. It then calls the production `prepare_handoff` gate: active grant, prior candidate/CSV SHA, authenticated local inventory, append receipt, journal and handoff must match. A missing or conflicting handoff, revoked consent or changed inventory refuses before provider I/O. The production `export_delta` subsequently checks grant currency per request and follows at most eight pages/200 records, requiring a complete deltaLink before publishing a new CSV and SHA-bound candidate. No automatic import, provider write, scheduled sync, durable checkpoint commit or retry is performed.

**Review the new CSV, removal markers and candidate before importing.** A successful next round only prepares another review candidate. A provider failure, incomplete round or uncertain local write needs human inspection and fresh artifact paths. The existing Graph token is never printed or persisted by this command. Provider tenant/mailbox identity remains operator-labeled, not independently verified; this does not establish real Microsoft Graph tenant acceptance, event-level attestation, automatic token renewal, or production retention approval.

Tests cover existing-handoff integration, all four independent consent requirements, revoked/mismatched gate refusal, absent handoff, output separation and the production export call contract. Real-provider acceptance and the full six-workflow native CI suite remain separate mandatory release gates.
