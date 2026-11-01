# Workflow explorer: review-before-copy agent handoff

This feature is a **local, read-only review step** between OWG's observed workflow map and an agent-neutral text request. It advances [#370 R5/M0](https://github.com/KAVentures/openworkgraph/issues/370), [#326 E](https://github.com/KAVentures/openworkgraph/issues/326) and [#264 stages 1, 5 and 6](https://github.com/KAVentures/openworkgraph/issues/264). It does not run an AI or shadow test, approve a workflow, or create an execution grant.

## Analyst journey

1. Explicitly opt in to OWG recording and capture consenting work through the existing controls. On **Overview**, select the explorer period and **Explore workflows**. OWG reads bounded, existing local evidence; it does not create a new collector.
2. Choose an inferred candidate, inspect separate exact variant lanes, and select a node. **Review selected examples** reads bounded supporting execution references. If a selected variant has no valid references, OWG does not substitute another variant's candidate-wide references.
3. Optionally use **Name or regroup selected examples** to save an explicit human-reviewed classification in the existing versioned grouping ledger. Saving requires its own exact-membership confirmation. A classification is not a verified business case or policy.
4. Click **Review safe agent handoff**. The review displays the selected period, variant, local step labels, omitted steps, bounded reference count, support-inspection status, and reviewed-group revision if present. The user must check the explicit acknowledgement before **Copy reviewed investigation request** becomes available.
5. **Copy non-executing shadow-test plan request** is enabled only for a saved reviewed group with a complete exact displayed variant, valid selected execution references, and a successful current selected-reference evidence inspection covering **every selected reference** (not merely a successful HTTP response or partial overlap), followed by the acknowledgement. It copies a *plan request*, not test results. A model, source system or test runner is never invoked by this UI.

The copied text deliberately omits raw window titles and displayed step labels; those may be sensitive or adversarial. It contains only bounded execution IDs, selected UTC evidence scope, exact reviewed revision when available, provenance/unknown warnings and read-only instructions. Users must review privacy before pasting it into any external AI. An investigation request may propose **authorized read-only** evidence reads; a shadow-plan request explicitly defers even those reads to a separately authorized future check. The recipient still needs its own current, consent-scoped authorization.

## Safety and stale-state rules

- A new period, candidate, selected node/variant, combined-map selection, or evidence inspection clears the open review and acknowledgement. A late previous evidence response cannot restore it.
- A candidate-wide selection is not silently upgraded into an exact variant. Clipped lanes are not represented as complete; missing refs remain missing. A saved grouping is a human classification, not authorization.
- Shadow-plan prompts explicitly request normal/exception examples, expected outputs, pass/fail/abstain criteria, current source-readback checks and rollback requirements **without tool invocation, execution or business writes**. Neither copied request establishes that any test ran or passed.
- No REST/MCP schema, capture, consent, tenant boundary, retention, canonical evidence, source identity, export ticket or business operation changes. No new server endpoint or remote destination.
- Copy can fail if clipboard permission is unavailable; the UI reports failure rather than success. No hidden upload or auto-execution exists.

## Acceptance and limitations

`tests/browser/explorer_accessibility.cjs` exercises the actual dashboard with a synthetic local evidence server and Chromium: keyboard-only candidate/node selection, WCAG 2.1 A/AA axe checks, review acknowledgement, unlinked-variant non-attribution, disabled shadow planning without current inspection or with mismatched returned refs, an explicitly reviewed and inspected synthetic group, exact revision pin, period invalidation, mobile width and no workflow writes. The synthetic inspected group uses a deliberately stubbed bounded response to test the UI state transition; it is **not** a real provider check.

Native exact-head CI, actual-browser/WCAG, OS, scale, packaging and release checks must pass before merging. This milestone does **not** complete the full evidence → reviewed design → idea → sandbox project → report lineage, execute a shadow validation, establish provider authority, meet real-customer UX acceptance, or demonstrate competitor parity/ROI. Those remain separate gates in #370/#326.
