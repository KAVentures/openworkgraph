# R18 — Deliver customer-run automation reports to OWG

A generated OWG Agents SDK project includes `send_report.py`, a **standard-library-only**, opt-in sender that connects the existing offline journal-to-report export with OWG's existing signed monitoring channel. It does not execute the process, approve cases, read private business tools, infer business outcomes, or run automatically.

## Prepare and review

First produce a monitoring report from a customer-run journal:

```sh
python monitor.py --journal execution-journal/INTENT_DIGEST.jsonl --deployment-name "My deployment" --output run-report.json
```

Inspect `run-report.json`. Reports contain *reported* outcome statuses, not proof of successful business processing. The sender preserves the report as an opaque payload and the server performs its own report validation.

In the OWG **Automation monitoring** dashboard, import or select the matching deployment, create a signed report channel, and securely copy its one-time 64-hex secret and channel ID. The API bearer token is separate from the channel secret and from any MCP/business-operation credential. Configure it only in the sender process environment, never in the ZIP, outbox or version control.

## Queue without network

```sh
python send_report.py queue --report run-report.json --outbox private-report-outbox.json --channel chn_YOUR_ID --version v1 --start-sequence 1
```

The first channel sequence is 1. For an existing channel, the start sequence must be chosen by the human from the channel's current `highest_sequence + 1`. Reusing an old sequence with different bytes will be rejected by OWG. On later `queue` commands to the same outbox, omit `--start-sequence`; the local outbox allocates increasing sequence numbers, capped to 53-bit-safe integers. **Do not delete or reconstruct an outbox to work around an uncertain delivery**. Preserve it until the receiver's state has been reconciled.

The private outbox holds the **original exact signed-envelope bytes** and their SHA-256 digests, not an OAuth token or signing secret. It is atomic on one filesystem but is not encrypted. The outbox path should be in an access-controlled directory on a trusted device and not in a shared Git repository. There is one outbox per channel, with at most 100 pending reports and a 16 MiB bound; a single report is bounded to 2 MiB.

## Explicit signed send

Set environment variables, using an HTTPS receiver or HTTP loopback `127.0.0.1` for locally running OWG:

```sh
export OWG_REPORT_RECEIVER_URL="http://127.0.0.1:8787/v1/automation-monitoring/channels/chn_YOUR_ID/reports"
export OWG_REPORT_API_TOKEN="YOUR_SEPARATE_LOCAL_API_TOKEN"
export OWG_REPORT_CHANNEL_SECRET="YOUR_ONE_TIME_CHANNEL_SECRET"
python send_report.py send --outbox private-report-outbox.json --confirm-network
```

No environment variables are needed to queue. Only this explicit `send --confirm-network` command can make the network request. The sender refuses redirects, proxy usage, unexpected URL paths, mismatched channel IDs, URL credentials, non-loopback plaintext and malformed responses. It sends only one pending envelope per invocation, preserving the submitted bytes and sequence number if the response is lost or not accepted.

After OWG replies with a matching accepted sequence and outcome, the sender removes that outbox entry and persists the next state. If the receiver imported a report but the network response was lost, rerun the same explicit send command against the **unchanged outbox**. OWG can then acknowledge an exact duplicate. If the receiver refuses the report due to version drift, revoked access, invalid signature, bad bytes or replay conflict, the outbox remains pending for human reconciliation; it never silently rewrites report bytes or automatically advances.

Rotating a channel secret means updating the environment credential before retrying (without changing staged report bytes). A revoked channel cannot be bypassed by the sender. A healthy receipt means **customer-reported execution was received**, not independently verified source success, achieved business value or authority to replay a business write.

## Validation limitations

Synthetic acceptance checks cover HMAC interoperability with OWG's real signed ingestion module, exact resend after a lost acknowledgement, tamper detection, URL restrictions, private local storage and no default sends. Production deployment, TLS trust, source outcome verification, independent customer sandbox validation and real tenant review remain separate gates under roadmap R18.
