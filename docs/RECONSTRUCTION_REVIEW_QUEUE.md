# Reconstruction risk-prioritized review queue

This local operator tool ranks the **existing production linker's ambiguous candidates** for inspection. It does not infer verified tasks, change canonical memberships, grant access, or execute business actions.

Run with an already authorized, locally held observation export:

```sh
python -m server.reconstruction_review_queue --events /private/observations.json --limit 100 > /private/review-queue.json
```

The input is a JSON list in the same shape consumed by `server.work_item_links.link_observations`. The queue uses only validated opaque event identifiers, normalized link relations, record identity, scope and taint flags. It never outputs titles, clipboard text, URLs, or observation payloads. The SHA-256 evidence digest matches the explicit-decision workflow in `server.reconstruction_reviewed_links`. **Do not interpret queue priority as probability, accuracy, or permission.**

The output distinguishes `inspect` from `conflicted`, with risk reasons including competing candidate components, incompatible explicit records, tainted evidence and missing/cross-scope claims. Items with risks remain visible for diagnosis, but must not be blindly accepted. Truncation is explicit; the operator must rerun with a higher limit (maximum 500) to inspect more. No persistence or upload occurs.

This feature improves bounded human triage, not autonomous assignment quality. The independent family-held-out F1 ≥ 0.90 and contamination ≤ 0.05 targets and real consenting-user acceptance remain unproven. Existing source consent, retention and revocation controls must govern the input file and any saved output.
