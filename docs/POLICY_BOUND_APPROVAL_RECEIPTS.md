# Policy-bound approval receipts

OpenWorkGraph can optionally harden the #65 human approval gate against policy changes that occur after a person approves an action but before the action executes.

This is an **opt-in adapter layer**. Existing `OptInActionGuard` behavior is unchanged.

## What it protects against

Suppose an agent proposes a structural deployment action under declared policy `change-control` version `3`. A human approves it. Before execution, the policy is replaced by version `4`.

Without snapshot binding, a later policy check can observe that an approval step exists but lose the fact that the person approved the *older* policy state.

`PolicyBoundApprovalGuard` closes that gap conservatively:

1. delegate the initial policy read and human approval flow to the existing #65 gate;
2. create a short-lived local receipt bound to:
   - workflow family;
   - proposed structural action;
   - declared policy ID;
   - declared policy version;
   - manifest SHA-256;
   - relevant structural rule IDs;
3. verify that receipt against #65's post-approval recheck;
4. perform one additional fresh policy advisory immediately before execution;
5. execute only when the receipt still matches and the approval prerequisite is still satisfied.

A policy version change, policy ID change, manifest replacement, policy removal, unavailable final policy read, receipt expiry, or action/family mismatch pauses execution and requires a fresh decision.

## Compatibility

This feature is intentionally additive:

- `adapters/action_guard.py` is not modified;
- no existing adapter automatically imports or enables the stronger guard;
- no database/schema changes;
- no server route or middleware changes;
- no new bearer token or network authority;
- no Gateway changes;
- no collector/browser/dashboard/MCP changes;
- no declared-policy authoring/signing changes.

Integrations opt in by importing `PolicyBoundApprovalGuard` from `adapters.policy_bound_approval`.

## Receipt semantics

`PolicyBoundApprovalReceipt` is **not**:

- a bearer authorization token;
- a cryptographic signature from the human approver;
- proof of human identity;
- reusable organization policy;
- persisted learned state.

It is an immutable, short-lived local consistency object. The default lifetime is two minutes and callers may configure a value from 1 to 900 seconds.

The receipt includes a non-secret fingerprint for stable local identification. Possession of the receipt grants no independent permission.

## Existing behavior remains unchanged

If no human approval is required, `PolicyBoundApprovalGuard` preserves #65's normal result and creates no receipt or extra final read.

If the human denies approval, the action remains denied exactly as in #65 and no receipt is issued.

Only a successfully granted approval enters the receipt-binding path.

## Warnings are still not hard-deny policy

This feature does not broaden enforcement. The same #65 boundary remains:

- an approval prerequisite may pause execution;
- `forbidden_step` remains advisory in this slice;
- non-approval predecessor warnings remain advisory;
- observed workflow frequency is never a permission source;
- context/outcome associations are never a permission source.

Receipt binding protects the freshness of an approval. It does not turn every declared policy warning into an execution block.

## TOCTOU limitation

The final fresh policy read is performed immediately before the wrapped zero-argument action is invoked, substantially narrowing the time-of-check/time-of-use window. It cannot create an atomic transaction between an arbitrary external action and a local policy file. Stronger guarantees would require the action system itself to participate in a transactional authorization protocol.

That stronger model is deliberately outside this slice.
