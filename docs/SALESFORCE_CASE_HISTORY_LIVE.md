# Salesforce CaseHistory: live selected HTTPS read → OWG event-log CSV

**Scope:** [#326 Section B](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 3](https://github.com/KAVentures/openworkgraph/issues/264), [#370 R3](https://github.com/KAVentures/openworkgraph/issues/370). This is a working **operator-invoked read-only connector**, not a continuous source sync, historical completeness guarantee, or Salesforce sandbox certification. The existing offline [CaseHistory converter](SALESFORCE_CASE_HISTORY_IMPORT.md) is reused for source-event IDs and minimization.

## Operator acceptance

With a current **read-only** Salesforce OAuth bearer scoped to the confirmed Salesforce instance and permitted CaseHistory rows, choose **1–8 literal Salesforce Case IDs** (15 or 18 characters), a stable organization namespace, and an inclusive UTC lower/exclusive UTC upper bound no more than 31 days apart. Only CaseHistory `created` and `Status` observations are requested. No actor, old value, subject, description, email or case body is requested. The selected source metadata can still be sensitive; obtain owner approval before extracting.

Create a **new** owner-only output directory (POSIX `chmod 700`); do not use a shared directory or a symlink. Use an environment-only bearer; never put it in CLI flags, logs or source control. Example placeholders:

```sh
mkdir -m 700 /private/owg-salesforce
export OWG_SALESFORCE_READ_TOKEN='<current-approved-read-only-bearer>'
python -m server.salesforce_case_history_live \
  --instance https://acme.my.salesforce.com \
  --confirm-host acme.my.salesforce.com \
  --namespace acme-sandbox \
  --case 500000000000001 \
  --since 2026-10-01T00:00:00Z \
  --until 2026-10-02T00:00:00Z \
  --output /private/owg-salesforce/history-001.csv \
  --consent-selected-read --consent-private-export
```

The command makes **one** Salesforce REST `GET /services/data/v60.0/query` with a validated, fixed-field SOQL query and a 201-row sentinel limit. It accepts at most 200 complete returned rows, 256 KiB of JSON and a single page. Redirects, proxies, pagination, partial counts, wrong source Case IDs, rows outside the time window, malformed timestamps, duplicate JSON keys, unauthorized responses and invalid statuses fail closed. No automatic retry. Only a **new** mode-0600 CSV is created; no existing output is overwritten. An empty selection returns `no_selected_history` and creates no CSV.

The result has columns `case_id,activity,timestamp,source_event_id`, with account-scoped hashed case and event IDs. The receipt contains counts only. In **Event logs → Import CSV**, select `case_id`, `activity`, `timestamp`, and optionally map `source_event_id` for deduplication under the **same stable source namespace**. This is a separate, explicit user action: the connector does **not** import into OWG automatically, schedule refreshes, contact agents, or perform provider/business writes.

## Boundaries and recovery

Salesforce CaseHistory retention, field history configuration, API privileges and record-level visibility can omit events. A returned page is not proof of complete source history, status transition ordering or a verified business outcome. The namespace is caller-declared, not cryptographically bound to a Salesforce organization. Source IDs are hashed for collision avoidance, **not anonymization**. Source revision timestamps and selected status labels may reveal business context.

The operator must rotate/revoke the provider token and delete retained CSVs according to local retention policy. There is no managed credential vault, centralized revocation, durable checkpoint, automatic late-event overlap, source deletion handling or scheduled incremental sync. Repeated manual windows are supported; the existing event-log import deduplication can skip repeated `source_event_id` values, but no automated checkpoint is advanced. On a refused/uncertain file write, inspect the private output path before choosing a new name; never assume an absent success receipt means no file exists.

## Verification

`tests/test_salesforce_case_history_live.py` exercises actual local TCP HTTP requests to a disposable Salesforce-shaped server and feeds the generated CSV through OWG's **production** `shared.event_log_formats.read_csv` parser. It checks the exact SOQL selection, privacy projection, duplicate/foreign records, incomplete pagination, HTTP errors, redirects, consent, output permissions and preflight refusal. These are synthetic source responses over real transport; **no authorized real Salesforce customer/sandbox acceptance has been performed**. Native Ubuntu/macOS/Windows pytest, browser, process collaboration, accessibility, scale, starter and packaging/release jobs remain required for merge and rollout.
