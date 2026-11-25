# Subscription and finance operations packs (R23)

Three versioned packs use the existing saved-log domain pack workflow: subscription
renewal cycles, loan application administration, and capital expenditure requests.
Select a single-case CSV (or equivalent case type), map exact recorded activity
labels, review editable elapsed-hour targets, install, and run the report.

| Pack | Timing | Optional evidence |
| --- | --- | --- |
| subscription_renewal | notice, renewal, billing, closure | review, retry |
| loan_application_operations | completeness, decision, communication, closure | clarification |
| capital_expenditure_request | budget review, decision, closure | rework, independent review |

The pack-specific sequence and elapsed-time checks use existing compliance and
reference-model engines. Lending and capital request separation-of-duties checks
compare recorded resource labels, not authenticated identities or authority.
Incomplete or exceptional cases can deviate legitimately; no pack checks payment,
underwriting criteria, regulatory obligations or actual spending authority. No
new provider calls, data access, writes or execution permissions are introduced.

Defaults are illustrative. Independent synthetic regression tests exercise import,
installation, report, changed thresholds, unmapped optional roles and uninstall.
Customer-specific source and policy acceptance remains separate.
