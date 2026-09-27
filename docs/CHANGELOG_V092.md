# OpenWorkGraph (unreleased, planned v0.92)

## Contextual name redaction for AI context: replace, don't remove

**What changes for you**
- AI apps connected through MCP now get **Redacted** context by default. Titles and labels keep their meaning, and only people and identifiers are replaced with stable tokens: `Re: Contract for PERSON_1A2B3C - Gmail`.
- Switch to **Full** under Connect → Connections → *AI context detail*. Your organization can lock it to Redacted.
- Every MCP response now includes `detail_level`.
- Exports gain **Redact names in export** (default off), using the same redactor.
- New **never redact** / **always redact** lists, editable in the dashboard or `config.json` (`ai_context.never_redact`, `ai_context.always_redact`).

**Three layers**
- **Raw:** local only, used for analysis, unchanged in storage.
- **Redacted:** new, the AI default.
- **Safe allowlist:** dashboard glance views and Gateway sharing, unchanged.

**Detection**
- Local name lists: US Census 1990, public domain; Statistics Sweden 2022, CC0.
- English and Swedish context rules, name pairs, coordinations, `Surname, First` order.
- Learned identities, plus stoplists for apps, UI vocabulary, months and organizations.
- Details and measured quality are in [OWNER_REDACTION.md](OWNER_REDACTION.md#contextual-redaction-for-ai-context).
- On the new 246-case evaluation set: name recall **0.982**, over-redaction **0.010**.

**Fixes**
- An identity learned from a `Name <email>` line while serving a response was forgotten by the next response, so later titles containing that name were not redacted.
  - Display-time learning now persists for the life of the process, still never written to disk on a read path.
  - The AI layer now reuses the learned token, so one person keeps one token.
- Several routes used by Context MCP tools (`/v1/tasks`, `/v1/summary`, `/v1/procedural-memory/*`, `/v1/task-context`) returned rich text without person redaction. All AI-context responses now pass through one redaction choke point.

**Organization policy**
- New Gateway policy key `force_redacted_ai_context`. It is restrictive: either side can force Redacted.
- The same lock is available as the administrator-managed `config.json` key `"organization_ai_context_detail": "redacted"`.

**Also**
- `tests/test_release_version_v087.py` expected 0.90.0 after the 0.91.0 release. It now matches the shipped version.
