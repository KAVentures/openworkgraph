# Mine explicitly selected Zendesk ticket status histories

The local Evidence workspace can read a small set of customer-selected Zendesk
ticket audits and project their standard status history into the same process
explorer used by CSV, Jira and GitHub. This is a separate, opt-in business-system
connector; it neither requires desktop observation nor associates tickets with
employees or observed desktop tasks.

## Administrator configuration

On the customer's local OWG runtime, configure:

```text
OWG_ZENDESK_PROCESS_LOGS_ENABLED=true
OWG_ZENDESK_SUBDOMAIN=your-account-subdomain
OWG_ZENDESK_OAUTH_TOKEN=<customer-managed OAuth access token>
```

The connector is disabled unless the first variable is exactly `true`. The
subdomain is a lowercase DNS label, not a URL. Requests are pinned to
`https://<subdomain>.zendesk.com` with TLS verification and existing proxy/CA
configuration. Tokens remain in the runtime environment; the browser and AI
bearer cannot submit or retrieve credentials. Invalid configuration makes no
provider requests. Use the customer's normal secret-management/runtime setup;
no credential is committed, copied into a process model or saved to OWG data.

Zendesk's current [Ticket Audits API](https://developer.zendesk.com/api-reference/ticketing/tickets/ticket_audits/)
requires an agent account with the OAuth `read` scope. Zendesk explicitly says
`tickets:read` and `auditlogs:read` do not authorize this endpoint. OWG does not
represent that broader provider scope as a per-ticket OAuth restriction; its
implementation issues only the explicitly selected ticket GET requests described
below. No inventory endpoint or write API is used. Configuration alone does not
start polling or source reads.

## Explicit user flow

Open **Evidence → Process mining · event logs → Read selected Zendesk ticket
status audits**. Enter 1–8 distinct positive numeric ticket IDs, authorize those
source reads, and select **Read selected Zendesk audits**. Entering IDs or
checking the confirmation makes no request. The IDs come from the user's
authorized source selection; OWG does not guess them from titles, timestamps,
neighboring resources, assignees or desktop history.

The result reports which selected positions produced temporary case labels and
how many supported status events were projected. Unavailable/mismatched cases
and incomplete/unknown audit-page coverage remain visible. **Explore selected
Zendesk status history** opens the existing maps, variants, frequency/gap
overlays, case examples and evidence counts without another provider read.

The projection is ephemeral and cannot be saved through CSV retention controls.
An explicit, separately reviewed TO-BE draft can be saved to the catalog with
`selected_source_audit` / `partial_source_audit` provenance and no native IDs.
Design save, map inspection and exporting add no Zendesk reads or write rights.
Changing the ticket selection clears the source result and invalidates pending
responses. No background scan, browser storage or credential fields are added.

## Source and projection semantics

For each selected ticket, OWG sends only:

```text
GET /api/v2/tickets/{selected_id}/audits
page=1..3&per_page=100&sort_order=asc
filter_events[]=Create&filter_events[]=Change
```

The documented event filter requests Create/Change events. Provider responses
can still contain metadata or other field changes; OWG immediately discards
comments, actor/IP data, custom-field values, attachments and other content in
memory before accumulating pages. It retains only bounded audit identifiers for
deduplication, exact selected-ticket membership checks, timestamps and standard
status fields during this request. No audit payload, ticket ID, audit/event ID,
actor, tenant hostname, credential or source content enters the returned report,
retained evidence, design or generated agent project.

Each audit must report the exact selected numeric `ticket_id`. A missing, malformed
or different ID discards that entire selected case, including earlier pages.
This establishes provider-reported ticket membership; it does not establish a
cross-system business-case join, employee handoff, causal dependency or business
intent. Cases remain separate selected tickets.

One unambiguous standard status Create or Change per audit produces a structural
label, for example **Created New**, **Status Pending**, **Status Solved**. Allowed
values are Zendesk's standard `new`, `open`, `pending`, `hold`, `solved`, `closed`.
Comment and non-status events are omitted. Multiple status changes in one audit
have ambiguous order and are omitted. Invalid timestamps/IDs/statuses and
conflicting duplicate audit IDs are omitted with diagnostics. Exact repeats of an
audit ID are deduplicated; distinct audit IDs at the same timestamp remain
separate events with unverified tie ordering.

Only status history is projected, even if the selected audit pages are exhausted.
Page exhaustion is not proof of a complete business workflow, current source
state, employee labor, waiting, successful customer outcome or agent causality.
There is no source-outcome receipt or business-execution authorization here.
Frequency/timing metrics describe only the explicitly selected projected cases.

## Bounds and failure behavior

- 1–8 selected IDs, 1–18 decimal digits each, no duplicates or leading zeroes.
- At most 3 pages / 300 audits per selected ticket and 100 audits per page.
- At most 512 KiB of decoded response bytes per page, including chunked or
  compressed responses; no implicit truncation into a valid history.
- Ten-second HTTP request timeout, 30-second overall source-read deadline,
  no automatic retries, no redirect following, no provider-supplied link fetch.
- A next-page link must identify the same HTTPS tenant/path/ticket and the next
  offset page. OWG reconstructs its own bounded URL and keeps the event filters;
  arbitrary links cannot redirect credentials. Missing pagination metadata stays
  unknown; reaching the page cap stays partial.
- Access denial or rate limiting stops the batch. Other unavailable selected
  cases are reported separately. Fixed errors do not echo provider response
  text, source IDs or credentials. JSON duplicate fields/nonstandard constants
  and malformed payloads are rejected.

HTTPX/httpcore request tracing is suppressed only within this connector's async
source-read scope to keep selected URLs and provider metadata out of debug logs.
Unrelated application requests retain their existing logging behavior.

`POST /v1/process-mining/zendesk-selected-cases` requires an authenticated local
dashboard session, a maximum 2 KiB strict JSON body and exactly
`{"ticket_ids":["17","19"],"confirm_source_reads":true}`. AI bearer credentials
are denied. Successful responses are `Cache-Control: no-store` and use
`openworkgraph.zendesk-selected-cases-process.v1`, with minimized selection
summaries, projection diagnostics and the existing process-mining projection.

Validation uses owned synthetic provider responses through the production HTTPX
client/projection path, plus the real dashboard/API/SQLite flow. Checks include
host/path/filter/OAuth behavior, wrong-ticket identity on later pages, pagination
caps and hostile links, redirects, duplicates/conflicts, ambiguous/invalid status
events, disabled/bad config, access/rate errors, response limits, logging,
authentication, strict bodies, stale UI responses, no automatic persistence,
separate reviewed design save and mobile. These tests do not certify a real
customer's Zendesk account, coverage or business acceptance.
