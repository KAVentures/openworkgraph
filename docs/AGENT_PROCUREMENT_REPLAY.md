# External candidate blind replay (R26)

Use a customer-reviewed shadow suite before any candidate has run. The
portable protocol uses OWG's existing generated offline evaluator to validate
the suite and create the frozen reference, rather than requiring an earlier
successful candidate or trusting candidate-supplied scores.

```sh
python -m shared.agent_procurement_replay prepare --process-export process.json --suite reviewed-cases.json --design design.json --confirm-export-inputs --out-dir trial
```

The design is the same prospective design in AGENT_PROCUREMENT_TRIALS.md,
but omit reference_report; OWG derives it from the reviewed suite. The new
private directory contains plan.json and challenge.private.json. The plan
contains minimized hashes and gates. The challenge contains case input values,
the runtime process specification, candidate aliases/configuration digests and
scheduled slots, **not** case categories, expected answers, review confirmation
fields, reference scores or gates. Keep the suite and plan separate from
candidate agents. This is separation by artifact, not a guarantee that an agent
cannot infer expectations from its inputs or access other files.

Only run external agents in a separately controlled no-write sandbox. OWG does
not launch them, grant tools, establish vendor identity, send data, enforce
sandboxing or observe execution. Explicitly review case values and process
content before sharing any challenge with a candidate. Prefer giving each
candidate only its assigned case and slot rather than the entire challenge.

Record the raw candidate response against its assigned slot, without replacing
a timeout or failed attempt. An envelope uses format
openworkgraph.procurement-replay-raw-captures.v1, plan_sha256 and captures.
Each capture has candidate_id, repeat, configuration_sha256, attempts. Each
attempt has case_id, slot_id (from challenge), status, cost_microunits and
elapsed_ms. A completed attempt also includes the exact returned plan object
(or malformed JSON value if actually returned). Other statuses must omit plan.
No agent-supplied report, scores or additional fields are accepted.

```json
{
  "format": "openworkgraph.procurement-replay-raw-captures.v1",
  "plan_sha256": "REPLACE_WITH_PLAN_SHA256",
  "captures": [{
    "candidate_id": "current",
    "repeat": 1,
    "configuration_sha256": "REPLACE_WITH_CONFIGURATION_SHA256",
    "attempts": [{
      "case_id": "case_1",
      "slot_id": "REPLACE_WITH_SLOT_SHA256",
      "status": "completed",
      "cost_microunits": 1000,
      "elapsed_ms": 2000,
      "plan": {
        "revision": 1,
        "content_sha256": "REPLACE_WITH_EXACT_PROCESS_HASH",
        "status": "proposal",
        "activity_ids": ["EXACT_ACTIVITY_ID"],
        "missing_business_inputs": []
      }
    }]
  }]
}
```

```sh
python -m shared.agent_procurement_replay score --process-export process.json --suite reviewed-cases.json --plan trial/plan.json --raw-captures raw-captures.json --out-dir scored
```

The installed trusted exporter creates a temporary generated project. A fixed
child interpreter invokes only its existing offline compare/validate_suite
functions; no provided program, model mode, raw output or external ZIP is
executed. Raw JSON passes via stdin, provider credential environment variables
are not inherited, and the temporary project is removed. The suite hash and
case identity must match the frozen plan; slot and candidate configuration
hashes must match. Actual returned plans are evaluated against the exact
reviewed expectation and graph, and invalid returned plans remain failures.
No delivered plan is silently dropped. Absent attempts remain unresolved.

The output directory contains captures.minimized.json (compatible with the
dashboard review) and review.json (all-enrolled review, plus an explicit
offline score-recomputation flag). Raw case values and model plan text are
excluded from both outputs. Files are exclusive, POSIX mode 0600 in a mode
0700 directory; Windows inherits directory ACLs. The original challenge, suite
and raw capture files remain under customer control and may contain sensitive
information. Hashes are identifiers, not anonymization.

The offline evaluator is a structural/customer expectation checker, not an
independent business outcome verifier. Actual runtime identity, scheduling,
usage cost, time and external candidate execution remain customer declarations.
No statistical independence, real-world outcome, automatic deployment or
execution authorization is established. No network or business call is made.
