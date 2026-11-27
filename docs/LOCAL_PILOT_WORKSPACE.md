# Local prospective pilot workspace and offline reviewer

This workflow extends the prospective cohort, counterbalanced execution and
blinded batch tooling. It is an opt-in **local evaluation workspace**, not
the OWG production recorder, an agent runner, a distributed study service,
or proof of real enterprise effectiveness. It makes no network requests.

## Operator workflow

Use the existing prospective cohort and execution commands to freeze a
plan and counterbalanced schedule, including the review protocol. Keep
the independently obtained plan and schedule pins. Use a private local
directory on a local disk; do not put this SQLite database in a synced
folder or share it between devices.

Initialize the database and retain the accepted head from the new output:

```sh
python -m evals.pilot_workspace initialize \
  --db study.db --plan plan.json --plan-sha256 "$PLAN_PIN" \
  --schedule schedule.json --schedule-sha256 "$SCHEDULE_PIN" \
  --out head-0.json --confirm
```

All commands take `--db`, a new `--out` path and explicit `--confirm`.
Every operation except initial status discovery also takes
`--head-sha256 "$HEAD"`. Here HEAD is the independently retained
`head_sha256` of the latest accepted workspace state. Each successful
mutation returns the full new accepted state with a new head. Keep that
pin outside the database. A stale pin is refused; inspect the latest
state and make a fresh decision rather than blindly retrying.

| Command | Additional arguments | Purpose |
| --- | --- | --- |
| status | optional head pin | Inspect the current accepted state |
| history | head pin | Inspect the retained operation/head chain |
| progress | head pin | Inspect execution cells and active/retired assignments |
| coverage | head pin | Full-denominator coverage without truth or review text |
| append | --actions actions.json | Apply exact execution actions transactionally |
| prepare-batch | --episodes episodes.jsonl --materials materials.json --packet reviewer.json --private-key key.json | Prepare and register a nonoverlapping approved batch |
| retire-batch | --batch-sha256 PIN | Retire an exact active batch before reissue |
| withdraw-episode | --episode-id ID --declared-at-ms MS | Stop remaining attempts and retire affected batches |
| reconcile | --episodes episodes.jsonl --batches reviewed-batches.json | Score only registered active batches against the accepted snapshot |

The action, material, private key, review and adjudication formats are
the ones in the existing cohort/execution/blinded-batch documentation.
A workspace does not execute an action in the real world, verify a
transport receipt, send an invitation, or authenticate a reviewer.

## Accepted-head semantics

SQLite uses BEGIN IMMEDIATE for each mutation. Concurrent writers using
the same old head cannot both advance that database. The complete
execution ledger is replayed, the current state digest is recomputed,
and the operation/head chain is checked on access. Historical workspace
state bodies are not retained; operation history is not independent
proof of what happened. This is not a distributed lock, cryptographic
signature, backup system or tamper-proof storage. An adversary who can
replace both the database and independently retained pins is outside
this integrity boundary.

The database retains the frozen roster and digests, declared execution
ledger, minimal review assignment inventory, and withdrawn episode IDs.
It does not retain prompt/response bodies, reviewer sheets, or actual
transport receipt bodies. IDs and digests can still be linkable.
The file is created exclusively with private permissions where supported;
the operator is responsible for OS access controls, disk encryption,
backups and required deletion.

Batch preparation writes the private key and approved packet exclusively
before accepting the new head. A file write or database commit can fail.
Any orphan files from a failed operation must not be distributed; check
the accepted inventory and securely remove unregistered files according
to policy. A failed CLI output write may follow a successful database
commit; inspect status before any retry. An active batch owns its cells
until retired. Retirement requires reissue with a fresh review ID.
An episode withdrawal retires every active batch containing it, including
mixed batches; reissue only the still-authorized unaffected entries.
Previously recorded terminal failures are preserved. Withdrawal cannot
recall a previously exported file, cancel an external model call, or
erase data from backups. Follow the actual study's retention instructions.

Reconciliation validates exact registered batches and the underlying
frozen ledger via the existing scorer bridge. Unsubmitted active batches
remain pending, withdrawn completed cells remain unknown, and incomplete
coverage withholds the canonical product report. Reconciliation is bound
to the accepted head at the start of the operation; check that head again
before using or distributing the result.

## Reviewer-only package

The operator can distribute the approved packet plus a local browser
interface without distributing private keys, truth files, source receipts
or ledgers. The UI can also be opened directly from
`evals/pilot_reviewer/index.html`. It supports ordinary blinded sheets
and disagreement resolution, explicit Boolean judgments, exact JSON
evidence ID arrays, partial review, resume from an exported sheet,
and exclusive user-initiated local download. Nothing is retained in
browser storage. Approved text is rendered as text, not markup.
There is no network connection or application server involved.

Obtain the independent packet digest from the registered batch key
(`packet_sha256`) or the disagreement basis
(`adjudication_packet_sha256`).

```sh
python -m evals.pilot_reviewer_package build \
  --packet reviewer.json --packet-sha256 "$PACKET_PIN" \
  --package reviewer.zip --receipt package-receipt.json --confirm

python -m evals.pilot_reviewer_package verify \
  --package reviewer.zip --package-sha256 "$PACKAGE_PIN" \
  --packet-sha256 "$PACKET_PIN" --receipt verified.json --confirm
```

Package verification reads the ZIP without extracting or executing it,
checks the independent archive and packet pins, exact member names and
metadata, exact installed UI bytes, canonical manifest and size limits.
The ZIP contains only the approved packet, three UI assets, manifest and
instructions. It is not encrypted. Distribute it through an approved
channel only after reviewing all text for disclosure and condition
neutrality. Do not send the batch key with it. Verify against the same
trusted OWG revision used to build the package. Reproducible archive bytes
are tested in one runtime, not promised across different compressor
versions.

Reviewers extract the verified archive into a private folder, open
index.html, select reviewer-packet.json, enter their declared reviewer
ID and save each judged entry. Unjudged cells are not exported as false.
Reviewers export the saved sheet and return it through the authorized
channel. An earlier sheet may be reopened for the same packet; the
operator must perform the final exact validation using the registered
private key. The browser does not verify identities, independent pins,
consent or the truth of review judgments.

## Verification and release boundary

Dedicated pilot CI runs the existing canonical scoring suite and the
workspace/package tests, plus a separate Chromium local-file journey for
review, resolution, injection-safe text, partial export and resume.
The local SQLite and ZIP algorithm checks are separate from CI. No real
participant, model or external system has been exercised by these tests.
R25 still needs a consenting real multi-team pilot, actual transport
receipts, independent outcome validation, and deployment acceptance.
