# Read-only process lineage summary (roadmap #370 R5/M0)

GET `/v1/human/processes/{workflow_id}/lineage` uses the existing human
organization-process principal and the same tenant-scoped repeatable-read
transaction as the current repository/catalog. Anonymous, admin service-token,
other-tenant and missing process-read-scope callers are not authorized.

This view compares **exact immutable revision and content SHA-256 pins**:
- the latest scoped process revision, with independent design-review status
  from the existing process catalog,
- the current saved opportunity and its process reference,
- the current saved implementation plan and its opportunity reference.

The view intentionally never follows an unvalidated arbitrary source
reference, never includes raw desktop observation/evidence records or
customer identifiers, and does not rewrite any saved artifact. It reports
missing, stale and corrupt data as incomplete rather than substituting
a nearby version.

`complete_current_declared_chain` is **not** an outcome verification or
permission. It requires current exact links and the existing independently
reviewed implementation baseline, but execution authorization, independent
evidence verification, business-outcome verification and new sharing are all
explicitly false.

This is one foundational R5 navigation/status slice only. It does not yet
include source-log/cohort references, evidence→idea→sandbox links, execution
receipts, actual workflow outcomes, cross-view UI, or customer acceptance.
