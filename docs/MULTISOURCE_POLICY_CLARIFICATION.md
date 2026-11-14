# R11 — Reconcile multiple policies against a reviewed process

OWG's local Process catalog now supports a guided review against **up to five separately provided policy/SOP sources**. A source can be pasted or loaded as a UTF-8 `.txt` or `.md` file (64 KiB per imported file, 60,000 characters per source). This adds an end-to-end user path to the existing exact-statement citation engine, rather than claiming automatic policy extraction or compliance.

## Use

1. Open an exact saved process revision in **Process catalog**.
2. Under **Clarify this design against multiple policy sources**, add each document separately, preserving its title/version and the exact source wording. OWG treats all text as untrusted evidence. The **full documents are not persisted** to the process database and no remote model is called for this analysis. **Selected cited excerpts and your human answers are retained** in the local decision ledger after you choose a decision. The response includes source titles, character offsets, and exact cited statement excerpts.
3. Choose **Find open questions across sources**. OWG flags unconfirmed business rules, missing responsibility/outcome definitions and conflicting statements about the same rule/step. Contradictions are conservative lexical heuristics; they are review prompts, not an independent legal or policy interpretation.
4. For a disputed rule or step, review every cited statement in context. To **Confirm** or **Revise**, select exactly one governing source statement (a radio choice) and write a human explanation for why that text governs this question. There is no preselected choice and the server independently refuses multiple, missing or unrelated choices.
5. **Reject** and **Defer** can leave the dispute unresolved, without falsely endorsing one statement. The decision ledger records the selected cited excerpt, document title, text offsets, answer, timestamp, exact process revision/content hash and source-bundle SHA-256. Decisions only suppress the same question for the **same exact source set**.
6. Change any source, reorder sources, or open a new process revision: previous decisions are no longer accepted as approvals of the changed material. A currently displayed question is invalidated when source text is edited. Saving a model confirmation requires the separate normal catalog save and review. No source document or question grants a tool, AI agent, or business operation authority.

## Guarantees and boundaries

The source-bundle fingerprint uses length-prefixed titles and source text, preserving order. It is a local integrity pin, **not** proof that a document is accurate, current, authorized or signed by a policy owner. Cited text may contain sensitive names or obligations. The browser holds the complete source text only in the current review; server responses show excerpts and the local decision ledger retains selected cited excerpts and explanations, so use a private authenticated local session. Deleting the saved process also removes its clarification ledger.

The backend rejects approval of a multi-way contradiction unless the reviewer explicitly selects **one** statement that appears among the disputed evidence and supplies an explanation. This avoids the former UI behavior of auto-including both incompatible sources in one recorded confirmation. Where several documents contradict each other, a human may need to amend or replace source material; OWG does not silently choose which organization policy is legally binding.

This does not yet provide OCR, document repositories, enterprise tenancy, effective-date precedence, signed provenance verification or automated multi-source reasoning. Those require separate product and real-world acceptance gates.

## Tests

Unit and real-HTTP regression cover three-way threshold/requirement conflicts, strict source selection, refusal of ambiguous endorsements, missing explanation, source-bundle changes and the unchanged saved process revision. Actual Chromium acceptance checks three authored SOPs, accessible source citation controls, no default selection, an explicit chosen citation and explanation, unchanged-document decision suppression, source-change invalidation, five-document limit and keyboard/mobile/WCAG. These tests do **not** establish correctness against independent real corporate policy documents.
