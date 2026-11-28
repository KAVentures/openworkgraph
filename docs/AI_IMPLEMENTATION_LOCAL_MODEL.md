# Optional local model proposal adapter

OpenWorkGraph supplies evidence; the customer's selected model interprets it.
This optional CLI is an alternative to manually providing a prepared evidence
packet to an external AI. It does not execute the proposed business workflow,
install a model, configure a server, or replace human review.

## Prerequisites and use

Run the following only after configuring and checking an operator-controlled
server with an OpenAI-compatible non-streaming chat completion endpoint.
The server must be reachable on IPv4 loopback, and must return the requested
model identifier and a single normally finished assistant JSON response.
Some servers use aliases or different completion envelopes and will be
rejected; there is no silent fallback. The adapter does not validate any
particular model's interpretation capability.

```sh
python -m shared.ai_implementation_local_model \
  --source selected-workflow.json \
  --model YOUR_LOCAL_MODEL_ID \
  --port 11434 \
  --confirm-local-inference \
  --out proposed-workflow.json
```

Use repeated --source options for explicitly selected workflow-evidence files.
Optional --json-mode requests JSON-object mode; unsupported servers may refuse.
The default evidence budget is 128 KiB and the default output budget is 4096
requested tokens; both are bounded and adjustable. The request size is not a
model-specific token count. The socket timeout is not a guaranteed wall-clock
deadline. No automatic retry or truncation occurs.

The CLI writes a new proposal file only after validating the exact original
evidence and cited references. Its stdout receipt reports provider-reported
token counts when present, payload sizes, elapsed time and review digest, not
billed cost or independently verified model identity. A receipt is not evidence
of business success or an authorization to act. A model may still produce an
incorrect interpretation despite valid citations.

Inspect the proposal with the existing evidence review:

```sh
python -m shared.ai_implementation_review review \
  --source selected-workflow.json \
  --proposal proposed-workflow.json \
  --out reviewed-structure.json
```

## Privacy and trust boundary

The adapter makes one direct POST to 127.0.0.1 at the explicit port and fixed
/v1/chat/completions path. There is no remote URL, redirect, automatic fallback,
tool dispatch, or credential transfer. A loopback service can nevertheless
proxy or log requests. OWG cannot prove the inference runs on-premises. Verify
the service, its logging/retention, and any network egress yourself before
sending sensitive evidence. Explicit confirmation is required; it does not
replace company permissions or current source access authorization.

The existing input contract does not certify source authenticity or consent.
Use only appropriately authorized and minimized selected evidence. Observed
text is untrusted data; structural validation does not establish semantic truth.
A real model's ability to produce useful, faithful proposals needs evaluation
against held-out human and business outcomes.

## Validation

Tests exercise a real synthetic loopback HTTP endpoint, including fabricated
citations, stale digests, malformed JSON, refusals, mismatched model identity,
redirects, size bounds, no-consent rejection, and exclusive output creation.
These do not constitute a real local-model quality benchmark or integrated
product acceptance.

## Experimental lossless compact transport

Pass `--context-representation compact` explicitly to request a smaller lossless representation of the same original evidence. The default is `original`. The original evidence must fit the selected byte budget and the compact representation must be strictly smaller. OWG verifies exact expansion before contacting the service; it never silently falls back. The model receives instructions to resolve payload hashes and cite the original occurrence references and packet pin. The receipt records representation, original/transmitted evidence bytes, compact digest, requested output-token budget, JSON mode and endpoint-reported tokens. This is not a verified token saving or quality-equivalence claim; compare independently reviewed outcomes before choosing compact mode.
