# Offline reconstruction event review queue

For a consent-authorized, saved-history `/v1/work-resumption` JSON response
containing `observed_pair_event_assignments` (PR #698), run locally:

```bash
python -m server.reconstruction_review_cli authorized-response.json > review-queue.json
```

The output lists each exact observed event and its candidate resource pairs,
separated by current/earlier observation window. Events co-referencing multiple
pairs are **ambiguous**, never assigned to the first pair. Events not present
in exact co-reference evidence are unassigned, not negative examples. Truncation
is carried forward. Every row requires human review and is **not a confirmed
case**; no provider access, upload, database mutation, agent execution or
business action occurs.

Keep input and output under the same consent, tenant, retention and revocation
controls as the source response. Do not use after grant revocation. This CLI
cannot itself validate a grant or reconstruct omitted events. The synthetic
tests do not demonstrate assignment F1, contamination or real-user acceptance.
