# Compatibility between the sectioned and integrated proposal contracts

The original twelve-section offline proposal contract (#1666) and its tests
are retained unchanged. The integrated implementation proposal is the format
used by the existing portfolio, planning, native skill and continuous
improvement journey (#1696). The contracts are not interchangeable: the
sectioned proposal explicitly records actors, trigger, inputs, outputs,
variants, policy and approvals, while the integrated format requires
model attribution, execution options and evaluation. An automatic field
conversion could lose uncertainty, policy distinctions or meaning.

## Re-author using the original evidence

Retain the original local workflow evidence export, sectioned proposal,
independent evidence pin and sectioned structural review pin. Run:

```sh
python -m shared.ai_implementation_compat prepare \
  --source original-evidence.json --sectioned-proposal prior-proposal.json \
  --evidence-sha256 ORIGINAL_EVIDENCE_PIN \
  --sectioned-review-sha256 ORIGINAL_SECTIONED_REVIEW_PIN \
  --output compatibility-handoff.json --confirm-local
```

The existing sectioned reviewer is rerun, then the existing integrated
packet builder runs on the same single original evidence export. All twelve
sections and exact original entry positions and scoped citation occurrences
are retained. The result contains the original sectioned proposal as
untrusted prior AI interpretation, the integrated evidence packet and
matching instructions. It may contain sensitive information. Review it
locally before any separate authorized external sharing; no model is called.
The combined canonical handoff has an 8 MiB limit, and the original source
must satisfy both contracts (including integrated packet bounds). Refuse
incompatible sources rather than dropping evidence.

Provide the handoff and existing integrated authoring instructions to an
authorized external AI and ask it to re-author a complete integrated v1
proposal. It must not treat the original proposal as observed work or policy,
and it must not invent execution options or model identity. If the evidence
is insufficient, request additional evidence instead.

## Review the returned proposal

```sh
python -m shared.ai_implementation_compat review \
  --source original-evidence.json --sectioned-proposal prior-proposal.json \
  --evidence-sha256 ORIGINAL_EVIDENCE_PIN \
  --sectioned-review-sha256 ORIGINAL_SECTIONED_REVIEW_PIN \
  --handoff-sha256 EXACT_HANDOFF_PIN --integrated-proposal returned.json \
  --output compatibility-review.json --confirm-local
```

The reviewer recomputes both original pins and the handoff, checks the
returned integrated proposal against the original evidence, and reports
each original section entry's citations absent from the returned proposal,
union citation retention and newly cited occurrences. An uncited original
entry remains in the report. Citation overlap is not semantic preservation:
a human must inspect original claims, especially unknowns, policy and
approval requirements, for semantic loss. No automatic migration or
approval occurs. Retain the original documents and use the existing
individual proposal review and shadow test process for any later action.

Outputs are private exclusive files; no new store, model invocation,
portfolio write, deployment or execution. The same compatibility path is available in the implementation dashboard. Prepare one original evidence source, expand **Optional: reuse an earlier sectioned proposal**, select the original sectioned proposal and enter its independently retained evidence and review SHA-256 pins. Download the exact compatibility handoff and review it locally before separately providing it to an authorized AI. Upload the returned integrated proposal to the local review panel, inspect per-entry citation omissions, and download the canonical compatibility review. Explicitly transfer the returned proposal into individual review; title, details and confirmation are reset. Editing source, budget, original proposal/pins or returned proposal clears stale compatibility review/transfer state. The original CLI remains available. The dashboard limits each proposal file to 1 MiB and the entire request to the existing 8 MiB request envelope; no data is silently truncated. Full combined CI and real external-AI
acceptance remain necessary.

```sh
python -m pytest -q tests/test_ai_implementation_review.py tests/test_ai_implementation_compat.py tests/test_external_ai_proposal.py
```
