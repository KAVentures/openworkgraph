# Data quality panel provenance and bounded diagnostics (#326 R6)

The local Data quality panel is a **read-only view** of supplied event-log
quality metadata, not evidence that a provider exported complete history.
It shows import/append batch digests and whole-log diagnostics without exposing
event attribute values.

- **Content v1** retains the original digest for compatibility. **Content v2**
  shows the separate delimiter-safe digest added in #444. The batch table shows
  each report's shortened digest, and the current whole-log footer shows the
  full v1 and v2 digests. Historical reports and older servers without v2 show
  a dash or explicitly say v2 was not recorded; no hash is fabricated.
- **Duplicate event payloads** compare stored activity, timestamp, resource,
  attributes and qualifier-aware object links, excluding the event ID (#446).
  Two events with matching case/activity/time/resource alone are not necessarily
  duplicates. This diagnostic does not delete or deduplicate records.
- **Attribute coverage** shows at most 32 keys, with counts but no values.
  The panel discloses the number of keys returned and the backend's total count
  and truncation flag (#448). Omitted keys must not be interpreted as absent.
  Older servers lacking those fields are labeled as having unknown truncation
  status; a zero displayed key count is not presented as an exhaustive inventory.
- **Future-dated events** retain the exact UTC reference instant and comparison
  basis from #449/#451. Live query-time counts are not called import-time counts.
- **Events without any retained object link** is the display name for the
  existing `events_without_case` API count. It counts events with no
  event-to-object relationship at all, **not** events missing an object with
  literal type `case`. OCEL object types such as `order` and `invoice` can
  each form their own case notion. An event linked only to an `order` is
  therefore not unlinked. This is a label clarification, not a redefinition
  of stored events or a new completeness guarantee.

A real-browser regression compares digest and count labels against the local
quality API. A separate *synthetic intercepted response* tests the 32-of-35
truncation disclosure; it does not assert that a customer/provider has 35
attributes or that a source export is complete. Backend DuckDB tests cover the
actual grouped-key counts. Neither fixture constitutes real-provider acceptance.

No source read, log mutation, retention/consent change, new authorization,
tenant scope, hash algorithm, or REST/MCP contract change is introduced.

The batch-trends panel reads `GET /v1/event-logs/{log_id}/quality/batch-history`
under the existing local dashboard owner session. It returns the same stored
reports, missing-report list and derived trends as the full quality endpoint,
without recomputing current whole-log diagnostics or content digests. Stored
reports include mapping and source metadata; they do not include sample event
values. The endpoint returns `Cache-Control: no-store`. It reads all retained
batch reports, so its work and response still grow with the number of batches.
The full quality endpoint remains available when a current snapshot is needed.

The dashboard uses `GET /v1/event-logs/{log_id}/quality/batch-history/page`
for at most 100 logical batches, including batches whose quality report is
missing. `before` is an exclusive batch-number cursor; `window.next_before`
provides the next older page. The newest and older controls navigate the stored
history. The previous report is consulted to compute the earliest visible
comparison, but is not returned with the page. The history is live, not a
stable snapshot, and can change between requests. The database must still
count and order batch identifiers; the bound applies to returned history and
application memory, not constant-time database work. The original full-history
and full-quality endpoints remain available.
