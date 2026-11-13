# Insurance claims operations domain pack (R23)

This optional pack adds a **reviewable operational view** to OWG's existing local process-mining workspace. It is available as `insurance_claims`, version `1.0.0`, through the same domain-pack catalog, installation form, report and uninstall endpoints as other R23 packs. It neither captures a browser nor connects to an insurer.

## What a process analyst can do

1. Explicitly import a user-authorized CSV/XES/OCEL event log into the existing local workspace. One case must represent one claim within a declared insurer/tenant namespace. The analyst is responsible for any necessary de-identification before import.
2. Select **Insurance claims operations** in the domain-pack catalog and map the log's actual activity labels to the required roles: claim received, triaged, assessed and decision recorded. Optional roles are additional-information requests/receipts, reopenings, payment records and closure.
3. Review/change the **elapsed** triage target (48 hours default) and elapsed decision target (30 days default). These defaults are illustrative process targets, **not insurer policy or law**.
4. Install. OWG pins the pack version, mapping, parameters and resolved definitions digest, and adds six typed case metrics. Run the report for the current saved log: time-to-triage, triage-to-assessment, time-to-decision, counts of information requests, reopen events and recorded payments. Rules review out-of-order transitions, target breaches, repeated/missing decision records, and optional payment/closure order. An object-centric reference lifecycle is also supplied.
5. Review **not applicable** sections for roles that were not mapped. The report stays source-unverified and descriptive. Reconfiguration uses the existing transactional replacement/history path; uninstall removes only this pack's owned metrics.

The same engine enforces real log labels, required mappings, finite targets, metric-name ownership, installation preconditions, version handling, deleted-log teardown and dashboard-session authentication. No new permissions or autonomous work are introduced.

## Limitations and safe interpretation

- One decision record is a **review assumption**. Appeals, reversals, amended decisions and multiple linked claims need a different approved model or explicit exceptions.
- An unpaid claim in a partial export is *not* necessarily overdue. A recorded payment is *not* bank settlement or entitlement. Event timestamps do not independently verify an insurer's final outcome.
- The pack cannot judge coverage, diagnosis, claimant conduct, eligibility, fraudulent behavior or fairness. It does not inspect claim or policy text and does not need claimant names.
- Imported records may still be sensitive. The user controls their import, storage, retention and independent sharing settings. Local analysis does not grant Gateway publication, MCP exposure or third-party action.
- All durations are wall-clock elapsed time. Business calendars, country-specific statutory claims processing deadlines, exception pauses and contractual SLAs are **not** inferred.
- Real insurer connector mapping and customer-controlled pilot acceptance remain outstanding. Synthetic tests establish program behavior, not validated industry correctness or functional parity.

## Regression coverage

`tests/test_event_log_domain_pack_insurance_claims.py` exercises a synthetic five-claim CSV with late triage, delayed decisions, missing/repeated decision records, premature decisions, reopening and optional payment; metric coverage; parameter overrides; optional-role omission; isolated owner metric survival; and invalid mappings and values. Run the repository's usual Python, native packaging and browser suites before merging.

Related roadmap: [#370](https://github.com/KAVentures/openworkgraph/issues/370), [#326](https://github.com/KAVentures/openworkgraph/issues/326).
