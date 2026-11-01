# Salesforce CaseHistory export import

The offline converter turns an explicitly selected Salesforce **CaseHistory**
CSV export into an event log usable in the existing OWG CSV import workspace.
It performs no provider requests, credential storage, background extraction or
business writes. It is an export integration, not a continuous connector.

Export the `Id`, `CaseId`, `CreatedDate`, `Field`, and `NewValue` columns from
CaseHistory using your authorized Salesforce export tooling. `OldValue` and
other columns may be present but are not copied. Use a UTF-8 CSV with unique
headers. Select the exact literal CaseIds you intend to analyze:

```sh
python scripts/salesforce_case_history.py case-history.csv \
  --account acme-support-sandbox \
  --case 500000000000001 --case 500000000000002 \
  --output selected-case-events.csv
```

The output path must be new. The JSON report printed to stdout contains only
counts and fixed projection metadata. Maximum input: 16 MiB, 100,000 rows,
32 selected cases. Malformed selected timestamps, conflicting projected values
for the same history ID and invalid selected status values refuse the export.
Rows for unselected cases and fields other than `created` and `Status` are
excluded and counted. No subject, description, old value, actor or message
content is projected. Status labels still describe business activity: review
the output before sharing.

In **Event logs → Import CSV**, map `case_id`, `activity` and `timestamp` to their
corresponding fields. If using supplied-source-ID deduplication for later
exports, map `source_event_id` to the source event ID column and retain the same
explicit source namespace. The account label must remain stable for a source;
use different labels for distinct Salesforce organizations. It is a declaration,
not verified provider identity. Case/event keys are account-scoped SHA-256
values. They prevent accidental namespace collision but are not anonymization.
15- and 18-character IDs are treated literally; do not mix representations or
case-fold Salesforce IDs across exports.

Creation and status histories are only a partial view of work. Availability
and field-history retention depend on the exporting organization. Missing
cases and events do not prove inactivity, completion or a complete process.
The report discloses represented cases, excluded rows and tied timestamps;
ties use deterministic event-key ordering, not known business chronology.
No automation or execution approval follows from this import. Provider sandbox,
continuous extraction, refresh/revocation and late-event acceptance remain open.
