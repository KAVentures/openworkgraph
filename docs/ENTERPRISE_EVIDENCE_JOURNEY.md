# Consolidated enterprise evidence journey

Roadmap #1572 and #370 (R3/R4/R5/R6/R10). This candidate builds on #1589
and integrates the evidence workflows from #1592, #1602, #1605, #1607,
#1610, #1613 and #1617 into a single follow-up tree. It does not update
the original branches or merge into the production release.

## One connected operator workflow

1. Publish exact, minimized source samples using the existing enterprise
   connector publication grant. Review source-level quality gates from
   Connector event integration → Publication evidence quality.
2. Select the same source publications in Reviewed cross-source cases,
   construct and preview the linkage. Review optional temporal bounds
   and declared timezone assumptions without fabricating strict order
   from business dates or tied timestamps.
3. Build exact conformance rules from the linked activity catalog.
   Investigate exceptions, optionally retain a grant-bound queue, and
   record a human disposition. Dispositions do not modify evidence,
   certify remediation, or authorize provider writes.
4. Export ordinary quality-reviewed JSON, a separately authorized
   derived linked JSON/CSV, or a full-source portable linked package
   with optional passing quality report. Full-source export includes
   excluded source rows and requires its own disclosure approval.
5. In the local Enterprise connector operations dashboard, import the
   ordinary or portable file. Verify manifests, complete original
   rows, quality report and (for portable linkage) the exact projected
   case namespace. Approve origin, local retention and applicable
   quality/linkage assertions. Receipts commit with the projected rows.
6. Review the local log and retained import receipt. A change to source
   scope, quality policy or linked projection requires a new log as
   documented in the relevant import workflow. The ordinary mining
   workspace remains available.

The combined linked-review dashboard exposes temporal, conformance,
triage creation, derived quality export and portable export controls
without losing any of the independent approvals. A changed plan resets
all derived reports and their consent. The separate quality and triage
consoles remain available.

## Integration fixes

- Both the live Gateway quality review and the offline verifier use the
  same typed policy and deterministic quality calculation. A frozen v1
  report assembly remains in the regression suite for compatibility.
- The ordinary and quality-gated linked exports use one shared projection
  under the existing organization transaction.
- Specific saved-review and exception-queue deletion routes precede
  the generic job/grant deletion pattern. This matters because the
  generic route has the same number of path segments and otherwise
  captures these requests before the correct handler.
- Quality report downloads preserve the original server response bytes
  rather than reserializing numbers through the browser. The portable
  import verifier hashes canonical JSON and distinguishes numerical
  representations such as integer and float. The exact report travels
  unchanged through the browser handoff.
- Combined route-role tests cover the original and new case endpoints.

## Acceptance

The full integrated enterprise acceptance job runs for this draft
branch, with existing native adapters, Gateway store, local workspace,
temporal, quality, conformance, triage and portable import tests. The
new backend journey fixture uses the real publication store, computes
quality and linked review, evaluates conformance, records a disposition,
exports both forms, imports actual original bundles into distinct local
mining logs, and checks grant revocation and log/receipt deletion.

The Gateway browser acceptance uses a single exact synthetic source
scope through failed/passing quality gates, reviewed linkage, temporal
analysis, exception investigation and both derived and portable export.
It saves three exact synthetic JSON downloads in a private runner
temporary directory. The independent local dashboard browser then
imports those downloads and verifies their file hashes and retained
quality receipts. The runner deletes the fixture files even on failure.
This fixture contains synthetic data only. It never uses customer
credentials or uploads real business records.

Static validation in the authoring environment checked all six committed
JavaScript sources (combined case review, quality, triage, both browser
scripts and local bundle import) with the V8 syntax parser. A static
inventory of the combined case-review HTML found 72 unique element
IDs, no duplicate IDs or missing static selector references. The route
inventory found 36 unique method/path declarations, and confirmed the
generic delete registration follows both specific delete paths. These
are static checks, not browser execution or integrated tests.

Direct git checkout remains blocked by DNS resolution and DuckDB is
not installed in the authoring environment. Full integrated CI, actual
browser execution and live provider/customer acceptance are pending.
Do not mark this candidate as release-ready before those checks pass.
The historical reconciliation, fleet and native provider expansion
branches are not included here; other enterprise workers own their
integration. Reconcile this candidate with those independently pending
branches before release. Offline digests do not authenticate an origin
or verify live grant status. A revoked publication invalidates live
review and queue access but does not erase authorized prior local copies.
