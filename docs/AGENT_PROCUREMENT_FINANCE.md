# Monitoring finance reconciliation

For each retained, closed monitoring window, a separate finance reviewer may
record a declaration of booked revenue and expenses under a single accounting
basis (cash or accrual). This is not a bank or accounting-system integration.
It does not independently verify documents, causal savings or realized ROI.

Use the local dashboard operation router-finance-reconcile or the existing
dashboard-only JSON-text document envelope at
POST /v1/process-catalog/procurement/router-finance-reconcile with workflow_id
and revision query parameters. The exact current revision, journal head,
monitor report and currency must match. The document contains:

- monitor_id, window_id, expected_head_sha256, expected_report_sha256;
- currency, reviewer_alias (distinct from coordinator), accounting_basis;
- period_start_ms, period_end_exclusive_ms (must exactly match frozen window);
- booked: exactly one row for each of the two arms (incumbent, challenger)
  and six categories (provider, external_services, incremental_payroll,
  implementation, other, revenue). Each row contains arm, category,
  amount_microunits (nonnegative integer, explicitly zero if none), and
  source_reference_sha256 (unique opaque reference to the finance evidence,
  not a hash of a low-entropy personal identifier);
- confirm_complete_period_bookings, confirm_disjoint_arm_and_category_accounting,
  confirm_exclude_imputed_labor_from_booked_cost, confirmed (all true).

The period must have ended both in the retained report and on the local
server clock. Repeated source references within a statement, across windows
or across retained journals are rejected. A single invoice allocated across
categories needs distinct independently reconciled line-item references;
different hashes cannot establish that the real financial amounts are disjoint.

The workbench records each arm's declared booked revenue, booked cost, net,
and net per enrolled case, including unobserved, failed and withdrawn cases
in the denominator. The difference between the two disjoint arms' net per
case is descriptive, not an actual counterfactual or annualized savings.
Telemetry direct cost and person-time are retained separately with unknown
bounds when measurements are missing. The diagnostic difference between
provider/external bookings and telemetry direct cost does not assume these
cost boundaries are identical. No imputed wage value is added to actual
bookings. No cash and accrual statements are combined.

Each reconciliation is immutable within its journal, tied to the exact
historical report and snapshot. Subsequent checkpoint evaluations preserve
the statement without recomputing it. One statement per window, up to 12;
corrections require separate external reconciliation rather than silently
overwriting retained evidence. Journal removal deletes the local statements
with the journal; external exports and backups are unaffected.

The overview shows which windows have a financial statement, not aggregate
booked ROI. Finance references and amounts appear in local journal detail and
exports; no raw invoice or account credentials are retained.
