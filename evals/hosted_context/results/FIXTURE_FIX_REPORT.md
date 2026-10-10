# Narrow fixture corrections and affected-case re-attempt

The original-corpus experiment and report are preserved in the parent evaluation
PR [#184](https://github.com/KAVentures/openworkgraph/pull/184). This separate
change corrects four demonstrable corpus defects, without changing case prompts,
target IDs, tool descriptions, product behavior or privacy policy.

| Case | Root cause | Correction | Authenticated regression observation |
| --- | --- | --- | --- |
| no-lexical-match | The fixture title contained the supposedly unmatched query | Use a synthetic exception title without “special request” | Lexical query returns zero; dated chronology still returns the target |
| many-events | Only endpoints 001 and 044 existed | Add 42 intermediate synthetic events within the original date bounds | First page returns 25, second 19; target 044 appears on page two |
| resource-pointer-only | Pricing event had no observable Acme association | Name the synthetic pricing sheet consistently with its prompt | Acme pricing search retrieves evt-price-001 |
| agent-retry | metadata.action was present, metadata.operation was absent | Add a structural tool_call operation, synthetic tool name and unknown status | get_agent_runs returns one execution; terminal outcome remains unknown |

The fixture now contains 288 events. The older target still lies outside the
last 200 and all original event IDs and case prompts are preserved. The agent
fixture does not manufacture success, failure, retry authorization or agent
text. The explicit synthetic sharing policy and hosted redaction remain intact.

**18 regression tests passed**, including authenticated production Gateway/MCP
handlers, pagination, lexical fallback, account isolation and evidence capture.
These are harness/corpus regression tests, not model evidence.

All four affected prompts were then resubmitted in fresh model sessions with
the same settings: Codex 0.159.2 requesting gpt-5.4 and Claude Code 2.1.296
requesting claude-sonnet-4-6; medium effort; both enabled and disabled conditions.
The rerun launched **16 sessions**. All eight enabled sessions initialized the
catalog, and **all 16 stopped on provider authentication before inference**.
There were zero model-origin calls, no completed turns, no harness_recorded=true
records and no model answers to grade. Invocation and task-improvement metrics
remain unmeasured. The corrected fixture SHA-256 and commands are recorded in
`2026-10-10-fixture-rerun/manifest.json` and `attempts.jsonl`.

Claude reported zero tokens and $0 for its eight attempts. Codex reported no
completed-turn accounting; its cost remains unknown. No immutable accepted
backend model snapshot is available because inference never occurred.

The main final matrix, excluded preliminary matrix and affected rerun together
submitted 192 CLI attempts, all authentication-blocked. This count does not
include two standalone authentication probes. It is an attempt count, not 192
completed model trials.

Remaining corpus limitations are intentionally not repaired by inventing more
successful observed work: ambiguous-resume's expected older editor event differs
from the newest filler tail; quotation title strings do not establish a genuine
multi-step repeated process. Agent-run derived references also do not by themselves
meet the event-shaped row requirement of the retrieval scorer; a raw trace can
establish exact event coverage. These need explicit oracle design and independent
review before a strong behavioral conclusion is possible.

**Verdict: still inconclusive.** The narrow fixes improve test validity. They do
not demonstrate automatic invocation or material AI work-continuity improvement.
No product/privacy change was made to improve benchmark scores.
