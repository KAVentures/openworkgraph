# Customer-operated external-agent loopback replay (R26)

The portable blind replay protocol has a concrete local HTTP collector. It
is intentionally a **customer-operated sandbox adapter**, not an OWG execution
grant, provider client, arbitrary command runner, or remote URL proxy. Start
your own no-write test adapter on 127.0.0.1. It may wrap an MCP agent, CLI or
provider under your organization's own separate authorization and isolation.
OWG cannot enforce what that process does with the case values, credentials,
network or tools. Use synthetic/authorized inputs and an independently
controlled sandbox, never a production action endpoint.

## Pin adapters before freezing the trial

Create adapters.json:

```json
{
  "format": "openworkgraph.procurement-loopback-adapters.v1",
  "adapters": [
    {
      "candidate_id": "current",
      "port": 9101,
      "path": "/plan",
      "timeout_ms": 30000,
      "backend_configuration_sha256": "REPLACE_WITH_CUSTOMER_CONFIGURATION_HASH"
    },
    {
      "candidate_id": "alternative",
      "port": 9102,
      "path": "/plan",
      "timeout_ms": 30000,
      "backend_configuration_sha256": "REPLACE_WITH_CUSTOMER_CONFIGURATION_HASH"
    }
  ]
}
```

Use the SHA256 of the reviewed backend configuration (without sharing
credentials); this is a declared identity pin, not attestation. Compute each
candidate configuration_sha256 before preparing a trial:

```python
import json
from shared.agent_procurement_http import adapter_digest
for a in json.load(open("adapters.json"))["adapters"]:
    print(a["candidate_id"], adapter_digest(a))
```

Use those hashes in the candidate design, then prepare the plan with the
existing replay CLI. The collector refuses missing, duplicated or changed
adapter configuration. Only literal IPv4 loopback, port 1024–65535,
bounded path without query or fragments, and a 100–120000 ms timeout are
supported. No redirects, proxy environment, arbitrary URL, authentication
header, candidate command execution or inherited provider credentials.

## Endpoint contract

The collector POSTs one JSON request of format
openworkgraph.procurement-http-request.v1, containing plan_sha256, slot_id,
candidate_id, configuration_sha256, repeat, case_id, input, process_spec,
execution_authorized:false, business_tools_authorized:false.
Only the assigned case is submitted; no case answers, category, other
candidate details, gates or reference scores are included.

Return HTTP 200 and an exact JSON envelope:
```json
{
  "format": "openworkgraph.procurement-http-response.v1",
  "slot_id": "EXACT_REQUEST_SLOT_ID",
  "plan": {
    "revision": 1,
    "content_sha256": "EXACT_PROCESS_HASH",
    "status": "proposal",
    "activity_ids": ["EXACT_ACTIVITY_ID"],
    "missing_business_inputs": []
  },
  "cost_microunits": null
}
```

Cost is the endpoint's declaration, not verified billing. Use the trial
currency and all-in cost accounting. Null means unknown. A valid envelope
with a malformed plan is retained for independent offline scoring.
Bad envelopes, oversized responses and non-200 HTTP responses are recorded
as unsuccessful transport/delivery; redirects are never followed. The
parent imposes a wall deadline on a fixed transport child and measures
elapsed time, including child overhead. A timeout does not guarantee the
remote endpoint stopped. No automatic retries.

## Collect bounded disjoint shards

```sh
python -m shared.agent_procurement_http run --process-export process.json --suite reviewed-cases.json --plan trial/plan.json --adapters adapters.json --offset 0 --count 20 --confirm-local-endpoint --out-dir shard-0
```

The run requires explicit confirmation of local sandbox endpoints and
authorized case submission. It validates the exact suite and plan before
network access. Each shard selects a contiguous 1–100 slot window in the
frozen schedule; a 4 MiB result budget may end a shard early. Each attempted
slot is written and fsynced before transmission, followed by its terminal
transport receipt, including raw plan when delivered. Directories and files
are exclusive/private on POSIX (Windows inherits ACLs). Do not share the raw
journal, which may contain sensitive model text.

If interrupted, inspect the retained journal. A started slot without a terminal
receipt is unresolved, not a successful run or evidence that no action
occurred. Never blindly retry it. A subsequent shard can start at an unstarted
slot; collection refuses duplicate started slots across all selected journals.
An incomplete trailing record is refused rather than silently repaired.
The chain detects content changes relative to its retained hash, not forgery
or deletion of a complete suffix. Preserve chain heads externally if needed.

```sh
python -m shared.agent_procurement_http collect --process-export process.json --suite reviewed-cases.json --plan trial/plan.json --adapters adapters.json --journal shard-0/attempts.private.jsonl --journal shard-20/attempts.private.jsonl --out-dir scored
```

Collection revalidates configuration, request hashes, exact scheduled order
within each shard, chain consistency and disjoint started slots. It exports
captures.raw.private.json, captures.minimized.json, receipts.json and
review.json, including offline recomputed scores and the local receipt digest.
All scheduled slots remain in the denominator; unstarted and unfinished
slots are unresolved. Maximum 100 journals, 24 MiB per journal and 8 MiB
combined raw ledger; excessive inputs fail explicitly, never sampled.
Receipts omit raw case input and raw plan text. The chain is not a signed or
independently authenticated history. It does not prove endpoint identity,
real model execution, independent costs, actual business outcomes, global
schedule order across shards, or no missing journal. No selection or
deployment is authorized. Production telemetry and stronger independent
attestation remain R26 follow-up.
