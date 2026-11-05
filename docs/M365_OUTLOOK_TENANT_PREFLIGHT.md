# Optional live Microsoft 365 tenant identity gate

This opt-in feature verifies the actual Microsoft Graph organization ID against a **separately obtained, exact expected Entra tenant GUID** before the existing verified mailbox/folder/delta export sequence. Unlike the earlier mailbox `/me` check, it checks tenant identity at the Graph organization endpoint. This is a real read-only Graph operation, not a local token-claim assertion.

## Permissions and privacy

The operator must independently confirm the expected tenant ID, hold a current exact OWG private folder grant, and supply an authorized delegated Graph bearer with permission to read `GET https://graph.microsoft.com/v1.0/organization?$select=id` (typically `Organization.Read.All` or `Directory.Read.All`, subject to Microsoft Graph consent requirements). Never broaden Graph permissions automatically. The operator must separately consent to Graph read, private report creation and tenant lookup. No credentials are stored or printed. Output contains SHA-256 hashes of the scope, tenant ID and response; it does not contain the raw tenant GUID.

The request is pinned to HTTPS Graph, disallows redirects via the existing transport, bounds the response to 8 KiB, requires one organization and exactly its ID, rejects duplicate JSON keys and rechecks the current exact grant before and after the read and before report creation. It performs no Graph or OWG writes and never commits a cursor.

## Use

Set `OWG_M365_GRAPH_TOKEN` in a secure ephemeral environment and create an owner-only private directory and exact grant as described in `docs/M365_OUTLOOK_VERIFIED_DELTA_ROUND.md`. Add these arguments to that command:

```sh
  --expected-tenant-id 11111111-2222-3333-4444-555555555555 \
  --tenant-report /private/tenant-preflight-001.json \
  --consent-tenant-lookup
```

The GUID above is a placeholder. The full verified-round command still requires `--consent-graph-read --consent-private-reports --consent-delta-export` and all its existing arguments. The tenant gate executes **before** mailbox, folder and delta reads. A missing consent, mismatched organization ID or unsafe response refuses extraction. The existing command without these optional flags retains its earlier behavior and reports `tenant_id_verified: false`.

Alternatively run `python -m server.m365_outlook_tenant_preflight --tenant ... --mailbox ... --folder ... --expected-tenant-id ... --grant-file ... --output /private/tenant-preflight-001.json --consent-graph-read --consent-private-report --consent-tenant-lookup`.

## Acceptance and limitations

Focused tests cover strict tenant parsing, ambiguity, duplicate keys, mismatch, consent refusal and ordering before delta. Real Graph sandbox testing, permissions verification against a customer-owned tenant, production OAuth lifecycle and end-to-end authenticated OWG import/checkpoint acceptance remain separate. A point-in-time Graph tenant match does not prove the business owns that tenant or authorize provider writes, retrying uncertain append operations, or automatic sync. Earlier private reports may remain after later-stage failure.
