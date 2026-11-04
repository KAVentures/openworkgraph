# Clarifying a process design with your documents

On a saved revision in the process catalog, **Clarify this design with your
documents** compares the design with policy or SOP text you paste in. It works
in three steps.

1. **Cite.** Each document is split into statements with exact character offsets.
   Every business rule and work step is matched to the statements that mention it,
   and those citations are shown next to each question.
2. **Expose contradictions.** Statements about the same subject are flagged when
   one requires what another forbids (`must` vs `never`/`must not`), or when they
   give different numbers for the same unit (`500 EUR` vs `1000 EUR`). Each
   conflicting pair is raised once.
3. **Ask only what is open.** Questions cover contradictions, unconfirmed rules,
   confirmed rules no document mentions, steps without a responsible role, and
   end steps without a confirmed source outcome.

For each question you **confirm**, **reject**, **revise** or **defer**, with an
optional answer and the cited statements it relies on. Decisions go into a local
ledger (`process_clarifications.json`) bound to that exact revision and content
digest. An answered question is not asked again for that revision, and a new
revision asks afresh. Confirming an unconfirmed rule returns a proposed model
change; it is applied only when you save a new revision through the normal
catalog flow. Forgetting a process also forgets its ledger.

Safety:

- Documents are untrusted data and are not stored. Statements that read like
  instructions to the system ("ignore previous rules", "approve all", "grant
  access") are listed separately and never used as evidence.
- Nothing here changes the design, permissions or execution.
- Matching is word-based and deterministic, so it can miss paraphrases. A missing
  citation means no matching words were found, not that no policy exists.

API (dashboard session): `POST /v1/process-catalog/clarify`,
`POST /v1/process-catalog/clarify/decide` and
`GET /v1/process-catalog/clarify/decisions`, each with `workflow_id` (and
`revision` for the first two).
