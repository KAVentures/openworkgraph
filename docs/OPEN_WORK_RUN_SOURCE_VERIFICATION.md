# Bound imported-run / fresh provider verification

This optional, session-authenticated POST endpoint compares one **operator-confirmed** imported automation run association with a fresh Stripe invoice-operation receipt and the existing reviewed historical evidence.

Path: `/v1/open-work/declarations/human-outcomes/verify-bound-run-source`

The exact JSON body fields are `work_item_ref`, `expected_revision`, `expected_edit_token`, `confirm_historical_review`, `confirm_run_comparison`, `confirm_source_verification`, `max_cases` (1–4), `deployment_id`, `run_id`, and `record_link`. All three confirmation values must be `true`. Use an active local dashboard `OWG-Session` authorization header, a signed source-joined record link issued by the existing Open Work flow, and a current operator-created run/case binding.

The route admits the source scope, reviewed declaration and dependency history, signed record link, imported run fingerprint and current actor binding **before** requesting provider evidence. After the source read it rechecks the source scope, full historical review, signed link, run fingerprint, binding and declaration guard. It does not create bindings, issue provider writes, authorize an agent or persist provider results.

A provider outage produces an unknown/unverified provider operation. Even a source-confirmed `paid` operation is **not** proof that the imported run caused payment, that a business objective was met, or that a customer outcome succeeded. Historical human assessments remain separate and are never silently promoted to independent verification. The response is `no-store`.

This is a functional API comparison, **not** independent run identity, atomic provider/local snapshot, customer-outcome certification, live provider pilot or A/B/C outcome uplift. Revocation and retention remain enforced through the existing binding and case admission paths.
