# Read-only source outcome verification

The first supported predicate is Stripe invoice paid in the frozen window.
It is intentionally narrow. It is not a general business-success judge.

In the journal workbench, use the closed-window blind case manifest to
prepare bindings (up to 10 per submission): slot_id, case_token_sha256,
resource_ref (existing opaque owg:r: reference to an observed Stripe invoice),
predicate = invoice_paid_in_window. Confirm both the case-to-invoice mapping
and that the declared case outcome is defined by this predicate. These are
operator declarations, not independently verified bindings. Bindings are
immutable and pinned to the current journal head and report; this occurs
after window close, not before execution, and may be subject to selection bias.
The local resolver must already know the invoice. The journal stores opaque
references and a locally keyed record-identity HMAC, never raw native IDs,
URLs, names, monetary amounts or source credentials. Exact reference and
resolved invoice identity reuse across retained journals is rejected. Changing
the local identity secret can invalidate existing bindings; no automatic
rebinding is performed.

Check linked Stripe invoice explicitly after binding. The existing opt-in
restricted Stripe account/record read adapter must be configured; otherwise
the result is unknown. The source check is read-only and uses a pinned
resolver snapshot, with no arbitrary URL. The server requires the same
head and report after the network request before retaining results.
The source read uses the adapter's account identity check and dated API.
Only a source-reported paid state with an internally consistent lifecycle
and a single paid transition wholly within the half-open monitoring window
counts as confirmed. Stripe timestamps have second precision; ambiguous
boundary seconds are excluded. Other states, unavailable sources, incomplete
lifecycle and transitions outside the window remain unknown, not failure.
A confirmed predicate conflicting with a declared failed outcome blocks
positive continuation and appears in the review queue. Source evidence
does not overwrite producer or separate reviewer declarations.

Unknown checks may be retried explicitly; confirmed checks are immutable.
At most 10 per request and 1,500 checks per journal, subject to journal
audit and state limits. No automatic source polling. No provider writes,
payments, bank-settlement proof, live-vs-test account attestation, safety
verification, causal attribution or general outcome verification. Source
checks do not satisfy the separate full-corroboration requirement.

API: router-source-outcome-bind and router-source-outcome-check, standard
dashboard-only JSON-text envelope with exact workflow/revision query.
Both take monitor_id, expected_head_sha256, expected_report_sha256,
window_id, confirmed=true. Bind adds confirm_operator_case_mapping=true,
confirm_case_outcome_defined_by_predicate=true, bindings array. Check adds
confirm_source_read=true and slot_ids (1–10). AI-context requests are refused.

## Jira issue resolution predicate

The same binding and check operations support `jira_issue_resolved_in_window`.
The resource reference must resolve to an observed Jira Cloud issue; the
identity pin includes its tenant hostname and issue key. An issue key is not
an immutable provider object ID across moves/renames or tenant migrations.
The existing Jira adapter requires explicit site opt-in and OAuth read scope,
checks authorized site and returned issue key, and returns a minimized current
status and resolution timestamp. A confirmed predicate requires current done
state, verified Jira operational resolution, and a resolution timestamp wholly
inside the frozen window (conservative whole-second boundaries). Missing or
inconsistent resolution, currently reopened issues, or out-of-window timestamps
remain unknown. This does not establish historical uninterrupted resolution,
customer acceptance, correct work, handoff, safety or general business success.

Both Stripe and Jira reads now go through the existing record graph's exact
retained-event authorization guard, including the exact provider/kind/host
and untruncated evidence requirement. A resolver entry alone does not permit
network access. No arbitrary source URL or native locator is accepted. A source
confirmation contradicting either a producer failure or a separately retained
reviewer failure blocks continuation. Post-hoc binding/selection limitations
still apply; no live account acceptance is claimed.
