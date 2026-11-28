# Campaign leadership measurement readiness

After comparing a frozen implementation campaign to the current portfolio,
expand **Leadership measurement readiness** and explicitly request a review.
The panel reuses the existing authenticated portfolio benefit-readiness engine,
including its portfolio-wide possible overlap scan, and then reloads the
portfolio to check that the selected members and their dependencies still
match the preceding campaign review. No new measurement or economic engine
is introduced.

All selected ideas remain visible: missing ideas, conflicting revisions,
ideas not recorded as implemented, missing measurement coverage, possible
overlap and unresolved source references. Only an existing per-idea
noncausal comparison that is ready within a complete portfolio scan can
display a reported monthly effort change at after volume. No combined hours,
savings or ROI are calculated; unrelated ideas are not exported. The global
scan bounds and completeness flags remain visible because overlap outside
the campaign matters. Quality, adoption and success criteria must still be
reviewed separately with the existing quality-benefit tools.

The readiness response and portfolio recheck are separate authenticated reads.
The panel rejects changes to the campaign member projection or dependencies,
but it cannot establish a single atomic snapshot, independently authenticate
measurements, or rule out measurement changes between the requests. Browser
timestamps indicate read completion, not independently signed dates. Exported
reports are local review artifacts, not verified business outcomes. Changing
or refreshing the campaign clears the outcome report. No writes, automated
model requests, approvals, or execution occur.

Tests: `node --test tests/js/ai_implementation_campaign*.test.mjs`.
The actual campaign and outcome panels were also exercised together in isolated
Chromium with substituted portfolio and readiness callbacks, including export,
stale revision refusal and invalidation. The full integrated browser test
covers the actual portfolio/readiness endpoints for a not-yet-implemented idea;
that test remains to be run in combined CI.
