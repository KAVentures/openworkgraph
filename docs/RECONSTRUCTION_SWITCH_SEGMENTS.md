# Switch-aware exact-record task segments (R1)

Run locally with an explicitly reviewed, consented JSON list of observed events:

```sh
python -m server.reconstruction_switch_segments --events ./consented-events.json --consent-local-review --idle-seconds 120
```

The input uses the same bounded event objects and exact scoped `metadata.resource_reference` as `server.reconstruction_switch_boundaries`. No external service or model is called. The CLI reads at most `MAX_BYTES` from the local file; the engine limits observations to `MAX_EVENTS` and output to 500 segments (default 200), exposing truncation.

Unlike idle-only `reconstruction_task_segments`, the algorithm splits a segment immediately when the observed exact business record changes. For A → B → A at seconds 0, 1, 2, output has three segments, with the final A segment carrying `observed_record_switch_return=true`, the prior A segment index and the intervening B reference. A → B → C has no return. A → A without an idle gap remains one segment. An idle gap can split one record but cannot prove an intervening task.

It excludes duplicate event IDs, tied timestamps (unreliable order), conflicting scopes for the same reference, invalid IDs, and observations without usable exact reference/time. Results are read-only, require local-review consent, and never update canonical membership or infer cross-record joins. Resource references are opaque identifiers; outputs are intended for authorized local review only, not automatic team sharing. They are **not** verified task boundaries, business outcomes, or a proof of assignment accuracy.

## Acceptance and limitations

`pytest -q tests/test_reconstruction_switch_segments.py` covers short A→B→A, no-return A→B→C, contiguous same-record work, idle-only resumption, ambiguity, conflicts, duplicates, and consent/bounds refusal. Full native CI must pass on the exact PR head before merge. Independently annotated family-held-out assignment F1 ≥ 0.90, contamination ≤ 0.05, interruption rejection and consenting-user pilot acceptance are separate, currently unproven requirements in #326/#370.
