# OpenWorkGraph hosted connector issue tracker (13 October 2026)

This is a repository-local follow-up to the seven synthetic/live MCP problems reported after PR #192/#193. The original external `openworkgraph-issues.md` was **not present in this GitHub repository**; this file does not overwrite any external note.

## Changes proposed in PR #194 (not verified in production until merged and deployed)

1. **PII leakage from Gmail targets and titles — addressed in code, live verification pending.** Identified person/organization/owner spans are pseudonymized in targets and titles while Swedish UI labels and PR references remain readable. Ambiguous one-word names can still be opaque. Third-party organizations with standard corporate suffixes are detected, and unrecognized names no longer rely only on name dictionaries in these fields. Add test accounts with unfamiliar first names, company names, and nested metadata; check all ten tools. This intentionally reduces textual context in the hosted AI response; the authorized server-side exact search retains source discoverability.
2. **Generic browser surface mislabeled Claude — addressed in code, live verification pending.** `surface:chrome` / `surface:browser` remain Browser independently of the current page host. Unknown hashed surfaces are not guessed.
3. **Verbose compact mode — addressed in code, live size comparison pending.** The default compact response removes repeated static paragraphs, but retains actor/privacy boundaries, security suppression counters, pagination, raw evidence and boolean authority flags. Explicit `rich` stays available, and `minimal` remains an opt-in.
4. **Recall via inference — improved, not solved.** Candidate clusters are ranked by quality without a default hard minimum; raw chronological evidence remains independent. Cross-app patterns are retained in hosted discovery instead of being split at every host switch. A distinct inferred family is still not a verified task.
5. **Goal-context tail saturation — improved, not solved.** Up to ten query-free recent raw events are always returned. Lexical probes page up to 300 results per term (800 matches total across probes) across permitted history, not just the most recent 500 rows. `source_limit_reached` and `lexical_probes_capped` continue to report incomplete coverage.
6. **Evidence drilldown — retained.** The existing `trace_lookup` timestamps/session IDs and query-free pagination remain intact. Old opaque execution references remain supported through the legacy fallback.
7. **Owner identity consistency — retained.** Profile email absent, name tokenized; bare `OWNER` text in observed captions is converted to the same owner-scoped token where possible.

## Still open after this PR

- **Full-history indexed lexical search.** The current SQLite/PostgreSQL implementation still uses bounded `LIKE` probes; the new code pages more matches but is not an exhaustive index. A separate, migration-tested, retention-safe full-text index is required before claiming complete search or latency gains. Never promote a no-match result to proof of no relevant historical work.
- **Real agent/client recall.** Unit tests and the synthetic `evals.raw_first_recall` are not evidence of real-world model behavior. Benchmark real ChatGPT and Claude with raw-only, inference-only, and combined retrieval. Verify whether the client actually invoked raw fallback and measure independently adjudicated task/resource recall, false matches, latency and token cost. Do not claim automatic retrieval from a tool instruction alone.
- **Uncaptured or locally-only work.** The hosted connector can only return data the desktop opted to sync and that still falls within retention. Privacy and the distinction between canonical local history and hosted redacted projections remain fundamental.

## Merge gate

Keep PR #194 draft until Python unit tests across Linux/macOS/Windows, browser and container CI, and package checks pass. After merge/deployment, test with an isolated synthetic OAuth user in ChatGPT *and* Claude, both compact and rich, with text consent both off/on. A GitHub merge or deployment alone does not prove the actual AI used the connector or achieved accurate recall.

### Private search policy

`search_work` uses redacted visible fields by default. An AI must explicitly set `include_private_matches=true` to match original title/target data, with the understanding that the match itself confirms presence of the private term. Authorized account scopes and consent for cloud text remain unchanged. No server can prevent an authorized client from drawing inferences from already-visible chronology; privacy is best-effort rather than mathematical noninterference.
