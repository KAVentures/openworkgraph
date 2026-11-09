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

## Concurrent checkpoint change during Graph read

The extraction gate now re-inspects the durable scoped checkpoint **after**
the delegated Microsoft Graph request and before reporting `review_required`.
The current generation and log must still equal the operator-reviewed values,
and the scoped provider grant is rechecked. If another operator commits a newer
checkpoint, or the grant is revoked, the command refuses to endorse the
candidate. Inspect the checkpoint and preflight evidence before considering
another explicitly approved extraction. **Do not automatically retry**: the
provider request may already have occurred, and generated private artifacts
may require manual disposition. This is a post-request race detector, **not**
an atomic lock or a guarantee of serializable Graph reads.


### Explicit second phase: reviewed checkpoint commit (Python API)

After the operator has separately inspected and appended the reviewed candidate
to the authorized OWG log, call
`server.m365_outlook_sync_readiness.commit_after_review(...)` with the
reviewed `expected_generation`, the exact previous candidate SHA-256, the
same scoped preflight evidence, private candidate/CSV/receipt/handoff paths,
authenticated dashboard session, and **all four** explicit confirmations:
`consent_local_read`, `confirm_removals_reviewed`,
`confirm_events_appended`, and `confirm_checkpoint_commit`.

The function does **not** append events or authorize a provider request.
It delegates to the existing durable checkpoint writer, which verifies
the reviewed handoff and actual authenticated OWG event membership before
committing the next generation with exclusive-file concurrency protection.
A changed predecessor, missing membership, replay, or ambiguous commit
response refuses; inspect the private ledger before retrying. This is a
Python operator API, **not yet a dashboard-integrated transaction**.
Real Microsoft tenant and end-to-end live OWG validation remain outstanding.


### Explicit operator checkpoint commit CLI

After the reviewed candidate has been separately appended to the authenticated OWG log, and its membership and removals have been reviewed, use the same module with `--commit-after-review` instead of `--extract-after-readiness`. Supply the common scope/preflight/grant/ledger arguments, `--expected-generation N`, `--confirm-log-id`, `--candidate`, `--csv-path`, `--receipt-file`, `--journal-dir`, `--handoff-dir`, `--successor-label`, and `--expected-previous-candidate-sha256` (the 64-character lowercase digest of the committed predecessor). Explicitly pass `--consent-local-read --confirm-removals-reviewed --confirm-events-appended --confirm-checkpoint-commit`. Set `OWG_DASHBOARD_SESSION` privately in the environment. No Graph token or Graph read/export consent is needed for this phase.

The two modes are mutually exclusive. This command does **not** append events; it delegates to the authenticated membership-verified checkpoint writer and refuses missing approvals, stale generations, mismatched log identity or ambiguous commit results. On uncertain outcomes, inspect the durable ledger and OWG log before any manual retry. Never interpret the CLI success message as proof of real Microsoft tenant validation.


### Reviewed append CLI and recovery (operator runbook)

After extracting a candidate, inspect the private CSV, candidate metadata,
removal markers and OWG log target **before** granting append consent. Run
`python -m server.m365_outlook_sync_readiness` with the common scope,
preflight, grant and ledger arguments described above, plus:

```text
--append-after-review --expected-generation N --confirm-log-id LOG_ID
--candidate NEW.json --csv-path NEW.csv
--consent-local-read --consent-local-append --confirm-removals-reviewed
```

Set `OWG_DASHBOARD_SESSION` in the process environment. Do not pass the
session, Graph token, source contents or other secrets as CLI arguments. The
append phase requires no Graph token and never advances the delta checkpoint.
It calls the authenticated OWG append adapter and checks the OWG inventory
readback. Only a verified append receipt allows the operator to consider the
**separate** checkpoint-commit command documented above. Retain the private
candidate, receipt, handoff and ledger evidence under the local retention
policy. Do not treat a successful append as evidence that a checkpoint moved.

If the command reports `unverified`, `possible_local_append`, or a
checkpoint-generation conflict, **do not rerun append blindly**. The POST may
have succeeded even if the response, inventory readback, grant recheck or
post-append checkpoint inspection failed. Inspect the authorized OWG log's
actual event membership and the durable ledger generation using the existing
authenticated read-only tools. Reconcile the candidate's event identities
against that inventory, check current grant validity and confirm the expected
predecessor before making a fresh, explicit operator decision. Never infer
that a failed response means no events were written. An uncertain checkpoint
commit similarly requires ledger inspection before any retry.

The three operator actions are intentionally mutually exclusive: Graph
extraction prepares a review candidate, reviewed append verifies OWG
membership, and checkpoint commit durably advances the cursor. This is a
manual CLI journey, **not** a transactional dashboard wizard, automated
recovery service, or proof of a real Microsoft 365 tenant integration. An
operator must also validate subsequent incremental extraction against the
newly committed generation. Provider credential expiry, revocation and
provider-specific failure paths require separate live-tenant acceptance.


### Private, immutable operator audit receipts

Supply `--audit-dir /path/to/private-audit` to any readiness, extraction,
reviewed append, or checkpoint-commit invocation. Provision the directory
**beforehand** with owner-only permissions (for example `mkdir -m 700`);
the operator refuses world/group-readable directories and symlinks. Each
successful operation creates a distinct mode-0600 JSON record using exclusive
creation and fsync, with action, UTC time, hashed scope and log identifiers,
reviewed generation, status, and bounded event/removal counts where available.
The record intentionally excludes provider tokens, session cookies, mailbox
addresses, message contents, and raw append receipts. Retain and protect this
directory under the organization's evidence retention policy; it is not a
remote Microsoft audit log or a substitute for OWG inventory verification.

If audit persistence fails **after** an append or checkpoint action, the CLI
reports an uncertain write and disallows blind retry. Inspect the authenticated
OWG inventory or durable ledger, respectively, and reconcile manually. The
`--audit-dir` option is optional for backwards compatibility; operators who
need durable evidence must explicitly enable it. This provides CLI-visible
audit artifacts, not yet a dashboard audit timeline.

**Platform limitation:** Audit receipt creation currently requires POSIX filesystem
permission semantics. On Windows it refuses before creating a receipt because
POSIX mode bits cannot establish private Windows ACLs. An append or checkpoint
operation with `--audit-dir` may already have written before this refusal; the
CLI reports an uncertain write and requires authenticated inventory or ledger
reconciliation. Do not assume that Windows audit persistence succeeded. An
ACL-aware implementation and native Windows validation are still required.
