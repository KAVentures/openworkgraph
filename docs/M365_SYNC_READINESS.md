# Microsoft 365 sync readiness

Run `python -m server.m365_outlook_sync_readiness --help`.

This opt-in local operator gate combines three previously independent artifacts:
a private mailbox identity report from `m365_outlook_mailbox_identity`, a
private folder metadata report from `m365_outlook_folder_preflight`, and the
latest durable checkpoint from `m365_outlook_checkpoint`.

Supply the exact tenant/mailbox/folder scope, expected mailbox address, log ID,
current grant, both reports, ledger directory and `--consent-local-read`.
All files must remain in private local storage. The command refuses missing or
mismatched evidence and revocation. Its output includes no Graph cursor,
mailbox address, provider identity or source data.

`operator_review_ready` means the three pieces of local evidence agree
for the configured scope. It does **not** mean provider data is complete,
the Microsoft tenant is verified, OAuth is refreshable, or a new Graph GET
is authorized. No provider calls, source writes, OWG appends or checkpoint
advancement occur. For actual next-round extraction, use the separate
checkpoint-anchored operator command with its own fresh permissions and
authenticated membership check. Existing reports can become stale; this
command does not prove their freshness or replace live provider verification.


### Bind operator review to a committed generation

Supply `--expected-generation N` after the operator has inspected generation N.
The readiness gate refuses if the durable checkpoint has moved to a different
generation, preventing stale operator decisions from being mistaken for fresh
approval. The option is backward compatible with existing local inspections;
for an operator confirmation/handoff, pass it explicitly. It does not authorize
Graph access, advance a checkpoint or replace a live provider-grant check.


### Combined preflight + checkpoint-bound next round (Python operator API)

The `extract_after_readiness(...)` entry point in
`server.m365_outlook_sync_readiness` now composes the scoped identity/folder
preflight and reviewed checkpoint generation with the already merged
`m365_outlook_checkpoint_next.extract_from_checkpoint` implementation.
Supply all required paths and identifiers, an exact positive integer
`expected_generation`, and separate explicit local-read, Graph-read,
local-export and removal-review consents. A mismatched preflight or missing
approval refuses before any provider request. The extraction delegate
independently checks the committed predecessor, authenticated OWG membership,
receipt and provider grant before a bounded Graph GET.

A successful return means **review required**, not sync complete. The
candidate must still be reviewed, appended with explicit approval, verified,
and checkpoint-committed in separate steps. No automatic append, retry or
checkpoint advancement is performed here. This programmatic composition is
not yet a dashboard workflow or a real Microsoft tenant acceptance test.


### Concurrent checkpoint generation guard

The combined operator API now requires the checkpoint-bound extraction delegate
also to report the **same exact committed predecessor generation** that the
operator reviewed. If a concurrent operator advanced the ledger between the
preflight and delegated extraction, the combined API refuses the result and
requires inspection before any manual retry. This is a fail-closed result
check, **not** an atomic lock: a bounded Graph GET may already have occurred
before the changed generation is detected. It does not authorize automatic
retry, event append, checkpoint commit, or removal. A full operator-facing
transaction and real-tenant acceptance are still outstanding.


### Operator command: generate a reviewed next-round candidate

The same module now provides an explicit command-line entry point for the
combined readiness and checkpoint-bound Graph read. This is an operator-facing
CLI, **not** an automatic import or a dashboard. Run
`python -m server.m365_outlook_sync_readiness --help` for the exact flags.

After inspecting the committed checkpoint generation and preflight reports,
provide the usual required scope and report flags, plus:

```text
--extract-after-readiness --expected-generation N --confirm-log-id LOG_ID
--previous-candidate PATH --previous-csv PATH --receipt-file PATH
--journal-dir PATH --handoff-dir PATH --successor-label LABEL
--output NEW.csv --candidate NEW.json
--consent-local-read --consent-delta-read --consent-local-export
--confirm-removals-reviewed
```

The command reads `OWG_M365_GRAPH_TOKEN` and `OWG_DASHBOARD_SESSION`
from its process environment; **never put credentials on the command line**.
Run only with private, owner-controlled artifacts and an authorized session.
It refuses missing review inputs, stale generations, missing credentials or
separate consents before delegation. The JSON output contains only a bounded
review summary or a generic refusal, never tokens, provider URLs, message
contents, or mailbox identifiers. Existing standalone readiness inspection
remains available without the extraction flag.

A successful result is `review_required`, **not** an imported event log.
An operator must separately inspect the generated candidate and removal
markers, explicitly authorize any append, verify the receipt, and commit
the next checkpoint. The CLI cannot make the Graph request atomic with a
checkpoint generation change, and does not prove live Microsoft tenant
acceptance. No automatic retry or checkpoint advancement is permitted.
