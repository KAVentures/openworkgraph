# OpenWorkGraph v0.113.0

## Frontier-aware automation interpretation without overreach

v0.113 improves how connected AI interprets OpenWorkGraph evidence while keeping the existing evidence-first architecture. OpenWorkGraph still does not decide what should be automated; it gives the consuming AI better factual context and a tighter reasoning contract.

### Compact evidence keeps work-surface identity

The compact MCP compatibility layer now keeps a bounded protected `work_surface` and `window_title` when available. Browser-heavy traces therefore retain distinctions such as Gmail, Salesforce and Google Sheets instead of collapsing to repeated `Google Chrome` rows.

Rich metadata remains omitted from compact rows. Surface/title strings are taken only after the secure runtime has applied the configured detail/redaction policy, and are bounded to keep the compact response small.

### Missing historical payload is not future infeasibility

Automation guidance now explicitly separates historical replayability from future automation feasibility. OpenWorkGraph may deliberately omit email bodies, typed text, clipboard contents and spreadsheet cells, but an authorized future agent may still retrieve the real inputs from Gmail, a CRM, files, databases, APIs or another live source system.

The consuming AI is told to check that execution-time route before treating absent historical content as an automation blocker.

### Outcome redesign and downstream dependency checks

For each material workflow, connected AI is asked to consider five design moves: eliminate a step, deterministic automation, agent delegation, agent plus approval, or keep the step human-only.

A possibly redundant step is treated as a hypothesis rather than a conclusion. Before recommending removal of a spreadsheet, report, handoff or other output, the AI should identify managers, controls, downstream teams or processes that may depend on it.

### Next autonomy boundary around existing agents

When Claude Code, Codex, ChatGPT, Cursor or another agent is already doing part of the work, the AI is asked to inspect what the human still does before, between and after agent runs. Typical candidates include routine prompting, copying outputs, checking tests/CI, creating a PR, monitoring completion and moving results between systems.

The guidance explicitly warns against recommending automation of a step the observed agent already performs.

### Capability mapping before rejection

Material opportunities should map required operations to the current AI environment as:

- **CONFIRMED** — available now;
- **PLAUSIBLE / TESTABLE** — a current route may work but is not yet verified;
- **BLOCKED** — a concrete access, policy, reliability, unsupported-system or input blocker exists.

Missing observation is not treated as proof of unavailability.

### Consequence-aware autonomy

v0.113 separates cautious trials from permanent per-action human approval. Low-impact reversible production actions may later use explicit scoped standing authorization where policy permits. Financial, regulated, clinical, safety-critical, irreversible or otherwise high-impact decisions/actions keep the appropriate human or organizational control. Repetition alone is never treated as permission.

### Fixed interpretation evaluation set

A six-case evaluation corpus now covers both major failure directions:

- **underestimation**, such as macro-only suggestions, treating missing content as a blocker, or missing the next autonomy boundary around an agent; and
- **overreach**, such as autonomous financial/clinical decisions, deleting a workflow step without checking consumers, or inventing automation for a one-off high-judgment task.

The repository includes a 0/1/2 criterion score format and a scorer that reports normalized underestimation, overreach and total scores plus critical `must_not` failures. The runbook specifies use of the real MCP entrypoint and exact model/client identifiers. Paid external model calls are intentionally not part of ordinary deterministic CI.

## Compatibility and privacy

- No canonical evidence schema change.
- No internal LLM or automation inference engine is added.
- No new MCP tool is added or renamed.
- The compact 12-tool surface and legacy compatibility entrypoint remain intact.
- Rich metadata, typed text, clipboard contents and hidden reasoning are not added to compact evidence.
- Automation judgments remain derived and disposable.
