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

## Prepare and review in the dashboard

In **Evidence → Large event logs → Salesforce CaseHistory export**, select an
export and enter the same stable account declaration and 1–32 exact case IDs.
Confirm local processing and choose **Prepare selected cases**. OWG reads the file
and performs the same bounded offline projection in the local dashboard server;
it makes no Salesforce request and does not store a log at this point.

The review shows aggregate row counts, the number of represented cases and a
preview of the first 20 projected events. The remaining count is disclosed. Only
creation and status events are included. Review the status labels before
selecting **I reviewed this projection** and **Use reviewed events in import
form**. The standard import controls then show the fixed mapping and supplied
source-event ID configuration. You must separately confirm local storage and
choose Import or Add file; staging alone does not retain events. Changing the
export, account or selected IDs clears a staged Salesforce file and its storage
confirmation. The review cannot create a live connection or a background job.

The dashboard derives a stable `sfexp_…` source namespace from the account
declaration. Repeat the exact declaration to deduplicate later manually prepared
exports. It is not authenticated account identity or anonymization. Live
connector-managed logs use a separate reviewed source scope; this manual flow
does not attach itself to those logs or advance their checkpoints. If combining
imports, check their source and case namespace mappings deliberately before
storing; the same activity imported under different source namespaces can appear
more than once. 15- and 18-character Salesforce IDs remain distinct.

The projection and its review are ephemeral until the separate import decision;
status labels can contain business information and are displayed locally. The
16 MiB file, 100,000-row and 32-case limits of the existing converter apply;
oversized preparation requests are refused. Source completeness and the order
of tied events remain unknown.
