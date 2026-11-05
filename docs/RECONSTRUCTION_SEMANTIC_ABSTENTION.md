# Semantic assignment: credible-competitor abstention

The consent-gated semantic assignment optimizer produces **review-only proposals**, not canonical case membership. The score manifest must match the current evidence digest and every pair must be eligible in the production review queue.

## Selection semantics

The algorithm first keeps the strongest eligible scored endpoint pair for each pair of deterministic components. A component cannot be selected twice. Pairs scoring below `MIN_SCORE` (currently 0.80) are not proposed and are **not counted as credible competitors** when calculating the `MIN_MARGIN` (currently 0.08) ambiguity veto. Two or more above-floor competitors within that margin still cause abstention for their shared component. Disjoint, above-floor pairs remain eligible for global maximum-weight matching.

For example, A–B at 0.84 and A–C at 0.79 permit A–B for human review, while A–B at 0.84 and A–C at 0.81 abstain. Neither result creates a link automatically.

## Verification and limitations

`tests/test_reconstruction_semantic_assignment.py` checks the threshold, competing high-score abstention, independent matches, component exclusivity through the existing integration fixtures, consent and digest refusal. This is a targeted recall correction to production proposal behavior, **not** proof of improved assignment F1, calibrated scores or customer value. Evaluate development families before any sealed family-holdout run; the preregistered F1 ≥ 0.90 and contamination ≤ 0.05 targets and real consenting-user validation remain outstanding. The upstream score provider and its authorization are not independently verified by this optimizer.
