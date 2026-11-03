# Reviewed ambiguous reconstruction links (local preview)

Roadmap: [#326 A](https://github.com/KAVentures/openworkgraph/issues/326), [#264 Stage 2](https://github.com/KAVentures/openworkgraph/issues/264), [#370 R1](https://github.com/KAVentures/openworkgraph/issues/370).

This is an **explicit human-review overlay**, not autonomous semantic inference, canonical case membership, or a verified business workflow. The production `link_observations` algorithm remains the only source of candidate edges. No source data, REST/MCP behavior, consent settings, business records or case membership are written.

## Operator workflow

On a locally retained, consent-authorized JSON array of OWG observations:

```sh
python -m server.reconstruction_reviewed_links --events /private/events.json > /private/candidates.json
```

Review the returned `candidates` and construct a separate local decisions JSON:

```json
{
  "version": 1,
  "evidence_sha256": "<exact digest from candidates.json>",
  "accepted": [
    {"event_id": "observation-b", "toward_event_id": "observation-a",
     "relation": "same_tab_unreferenced"}
  ]
}
```

```sh
python -m server.reconstruction_reviewed_links --events /private/events.json --decisions /private/decisions.json > /private/reviewed.json
```

An accepted edge must be a **literal candidate in the current evidence**. All decisions are validated before application; duplicate/unknown edges, stale source digests, malformed input, explicit record-identity conflicts, tainted claims, and actor/device/session/tenant scope mismatches refuse. Accepted proposals produce `proposed_groups` and `reviewed_edges`; the canonical linker and stored evidence are never changed. Empty accepted list leaves canonical groups unchanged.

The digest binds to normalized admitted observations, source groups, and candidate edges, including time, source, scope and opaque record identity. It is a stale-evidence check, **not** a cryptographic signature or proof of user authorization. Do not publish event IDs, digests or private local JSON files. A digest alone is insufficient consent. Use only the locally authorized retention/consent window, delete local exports according to policy, and never treat this CLI as permission to disclose or execute.

Limits: 10,000 input rows, 500 explicit review decisions, 8 MB per input file. The CLI returns no event titles, clipboard contents, URLs or labels. Reviews across different explicit record identities are intentionally refused even when a human believes two records describe the same business case: that requires a separately authorized source-backed identity join.

## Verification and gaps

```sh
python -m pytest tests/test_reconstruction_reviewed_links.py -q
```

This adds a reviewable route to improve **human-assisted** membership coverage, not a demonstrated increase in autonomous holdout F1. The family holdout gate (F1 ≥ 0.90; contamination ≤ 0.05), interruption rejection, and a consenting real-user pilot remain independent release evidence. No model API calls, paid evaluation, automatic merges or external writes occur.
