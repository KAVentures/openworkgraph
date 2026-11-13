# R16 — Portfolio realized-benefit evidence readiness

The existing automation pipeline records manually reviewed opportunities, implementation decisions, independently entered **baseline/after** effort measurements, coverage-aware per-case comparisons and candidate duplicate sources across ideas. Those inputs are useful, but inspecting them one idea at a time makes it easy to overlook missing evidence or silently treat a modeled estimate as realized savings.

This release adds one **dashboard-session-only**, read-only portfolio worklist at `GET /v1/automation-pipeline/benefit-readiness`, available through **Review benefit evidence readiness** on the Automation pipeline. The source is the existing local SQLite pipeline and measurement store. It is not a new measurement collector, finance ledger or execution process.

## Review categories

Every locally implemented idea receives one primary next-action category. The order is intentional and fail-closed:

- **Unreadable evidence:** a stored measurement is malformed or fails the current strict measurement schema. Do not assume missing entries were zero.
- **Missing baseline / after:** no comparable before or after periods. A state of *implemented* is a human declaration, not evidence of measured effect.
- **Overlapping periods:** within-idea baseline or after date windows overlap, so pooling would double-count exposure.
- **Zero-case period:** a per-case comparison has no valid denominator.
- **Cross-idea overlap candidate:** two different implemented ideas declare the same exact source kind and nonempty reference across overlapping after-date windows. They may cover the same cases; the candidate is not proof of duplication.
- **Missing source reference:** a period is entered without an exact declared source reference. It may still be useful for local review, but the portfolio worklist flags the provenance gap.
- **Ready for noncausal comparison:** complete nonoverlapping positive-denominator periods, references declared and no detected exact-source conflict. Only here does the view show a bounded **reported** per-case effort difference at the later volume, in hours/month. It is never called savings.

An unchanged source string does not independently prove identity, and different source strings can still describe the same work. An empty candidate list does **not** mean cases are distinct or benefits can be summed. The source evidence is user entered, may be incomplete and does not verify automation causality.

## Safety and bounded results

The viewer shows at most **150 matching rows** at once, with a local readiness filter and case-insensitive search, a separate count of matching and displayed rows, and a next human review action. The API scans a bounded maximum of **1,000 implemented ideas × 48 recorded measurements**. It reads the implemented cohort and measurements under the existing pipeline/benefit locks in one local SQLite view. A malformed or truncated record suppresses unsupported comparisons.

Only status, source **kind**, missing reference counts, exact local idea IDs/titles and limited reported effort changes are returned to the local authenticated dashboard. Raw measurement notes, exact source reference values, case/person identifiers, receipts, tool credentials and provider data are not returned. Response caching is disabled.

The output sets `causal_effect_verified=false`, `realized_savings_verified=false`, `portfolio_savings_summed=false`, `execution_authorized=false` and `source_reference_values_returned=false`. No currency conversion, self-reported monetary ROI or portfolio-level additive benefit is asserted. The *ready* category means ready for **human evidence comparison**, not independently verified actual savings.

## Test evidence

Regression covers empty portfolio, all major missing/invalid categories, exact after-source overlap across two ideas, suppression of candidate overlap effort comparisons, malformed legacy rows, exclusion of unimplemented ideas, local browser-session authentication and no-cache semantics. Real Chromium acceptance uses the existing implemented idea baseline/after flow, reviews missing source references, filters the portfolio, removes an after measurement and confirms its readiness changes to *missing after*, with accessibility and mobile checks.

Independent finance verification, customer source readbacks, real operational baselines, adoption/cohort controls and audited benefit attribution remain separate R16/R25 acceptance requirements.
