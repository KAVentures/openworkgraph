# Global opaque-tag reconstruction proposals

This opt-in offline command combines the existing production ambiguous-candidate
queue, opaque operator-approved category tags and the merged maximum-weight
nonoverlapping assignment engine. Unlike the older greedy tag scorer, it can
choose two mutually compatible candidates whose combined evidence score exceeds
one individually higher-scored candidate.

```bash
python -m server.reconstruction_tag_global_assignment \
  --events /private/observations.json \
  --tags /private/opaque-tags.json --consent-local-tags
python -m unittest tests.test_reconstruction_tag_global_assignment
```

The events input is the same local JSON observation list accepted by the
reconstruction review queue. The tags input is a JSON object mapping literal
event IDs to arrays of 2–16 bounded opaque category IDs, e.g.
`{"event-a":["invoice","review"],"event-b":["invoice","review"]}`.
Do not place raw text, customer names, patient data, record identifiers or
provider secrets in tag values. Generate tags only from independently
authorized, current evidence; this command does not acquire that authorization.

Only existing review candidates are scored. Unsafe source/scope/record
conflicts are vetoed, incomplete queues fail closed, and ambiguous competing
scores abstain. The global optimizer requires scores >= 0.80 and a competing
margin >= 0.08. It returns evidence-digest-bound review proposals; there is no
case mutation, automatic approval, network request or external model call.
A changed input invalidates the digest. A reviewer must still inspect each
proposal and independently verify its source identity.

This is a deterministic Jaccard category overlap baseline, not autonomous
semantic extraction, calibrated confidence, verified case membership or
measured customer accuracy. Do not claim the family-held-out F1 >= 0.90,
contamination <= 0.05, interruption-rejection or real pilot gates until they
are independently evaluated. Existing REST/MCP/dashboard and consent policies
are unchanged.
