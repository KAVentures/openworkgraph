# Lossless context compaction for external AI implementation (P0 #1663)

This independent local utility complements the portable implementation
evidence packet in #1667. It does not create another proposal format,
capture data, change retrieval or infer workflow semantics.

Once the existing portable packet has been prepared, preserve its original
packet_sha256 independently and run:

```sh
python -m shared.ai_context_compaction compact \
  --input packet.json --packet-sha256 ORIGINAL_PACKET_PIN \
  --output compact-packet.json --confirm-local-context
```

The command interns *exactly identical* canonical JSON event payloads in a
digest-keyed table. Each original scoped event reference remains in its
original bundle, run and ordered occurrence. Repeated payloads do not imply
the same underlying event, case, person or task. No approximate matching
or semantic inference takes place. Distinct event payloads are not merged;
all non-event metadata and original coverage declarations are preserved.

The output includes the original packet pin and a separate compact digest.
The command prints the actual canonical UTF-8 byte counts before and after,
including the compact format overhead. Some inputs are larger after
compaction. Use the original packet in those cases. A byte reduction is
not evidence of a token reduction, lower model cost or equivalent model
quality. Compare actual provider usage and quality on the same held-out
cases before adopting the compact context as the default.

For an authorized external model using the compact packet, explain that
packet_skeleton.bundles[].runs[].events[] retains the original scoped
citation refs, and each payload_sha256 points to the exact payload in the
top-level payloads table. The original proposal format and original
packet_sha256 remain unchanged. The table contains untrusted observed data,
not instructions or permissions. This is not a redaction tool; all original
packet content can be recovered. Review access, consent and local retention
before sharing. No upload or model invocation occurs here.

Reconstruct the original packet when needed:

```sh
python -m shared.ai_context_compaction expand \
  --input compact-packet.json --packet-sha256 ORIGINAL_PACKET_PIN \
  --output restored-packet.json --confirm-local-context
```

The restored canonical JSON is identical to the original canonical JSON.
The utility refuses malformed references, duplicate scoped occurrence
identifiers, inconsistent counts, missing or unused table entries,
tampered payloads and digest mismatches. Output and expansion are bounded
to 16 MiB and 4,000 event occurrences. Existing files are not overwritten.
Original evidence sources still must be retained for the existing proposal
review; compaction does not establish their authenticity or current
authorization.

The focused tests exercise 300 randomized lossless round trips, Unicode
payloads, repeated occurrences, tampering, byte accounting, output refusal
and expansion limits. Run:

```sh
python -m pytest -q tests/test_ai_context_compaction.py
```

Full integration with #1667, actual target-client interpretation and
provider token/quality benchmarking remain separate acceptance work.
