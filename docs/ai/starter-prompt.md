# OpenWorkGraph starter prompt

Use the attached OpenWorkGraph evidence to reconstruct how I actually performed my work. Treat raw observations as evidence, not as pre-interpreted tasks.

## Evidence priority

1. **RAW local evidence is the primary source whenever it is included.** Reconstruct tasks, task boundaries, projects, outcomes, repeated workflows, and information handoffs yourself from the chronological raw event stream.
2. Use operational events / semantic activity as compact indexes that help you navigate the evidence.
3. Treat OpenWorkGraph's inferred tasks, repeated task families, summaries, and automation suggestions as **non-authoritative heuristics only**. Do not repeat them merely because they exist. Replace them when the raw evidence supports a better reconstruction.

Read the raw rows chronologically and use timestamps, app/window/page identity, safe target labels, focus spans, engaged time, keypress/click/scroll counts, transitions, navigation events, and `metadata_json` together. Important metadata may include `tab_id`, `tab_context_id`, `browser_session_id`, `semantic_action`, `semantic_action_confidence`, `clipboard_transfer_id`, `linked_copy_event_id`, `clipboard_source_tab_context_id`, and `clipboard_link_age_seconds`.

Copy/cut/paste **behavior may be captured, but clipboard contents are not**. A shared `clipboard_transfer_id` is evidence that a captured copy/cut and paste belong to the same observed transfer. Desktop and browser sensors can both observe the same action, so coalesce near-simultaneous corroborating events rather than double-counting them.

Do not assume foreground duration equals active work. Distinguish real engagement from windows left open while idle. Do not invent intent, content, recipients, outcomes, or implementation details that the evidence does not establish; state uncertainty explicitly.

Combine this workflow evidence with any other context and tools legitimately available to you, such as documents, code repositories, email, project systems, prior conversations, APIs, and enterprise knowledge.

## Automation frontier

When looking for automation opportunities, do **not** rely only on your pretrained intuition about what AI can automate. Automation capabilities change quickly and may be broader than traditional macros, templates, fixed RPA, or simple integrations.

Before deciding feasibility, inspect the capabilities actually available in your current environment: connected tools and APIs, MCP servers, browser/computer interaction, file/document/spreadsheet access, code execution, scheduling or conditional tasks, and human-approval mechanisms. If current verification is available and capability details may have changed, use it. Do not assume a capability exists just because modern agents sometimes have it, and do not assume a capability is unavailable merely because OpenWorkGraph has not observed it before.

Reason about automating the **outcome**, not necessarily reproducing every human click. Consider direct APIs/connectors, code or file operations, browser/computer use, deterministic plus agentic hybrids, and approval checkpoints.

Classify material automation opportunities as:

- **Ready to automate** — the required capabilities are actually available and the workflow and side-effect boundaries are sufficiently understood.
- **Test** — a current agentic approach is plausible, but reliability, permissions, edge cases, or workflow reconstruction remain uncertain. Uncertainty about modern AI capability is a reason to test, not to dismiss the opportunity.
- **Not currently practical** — there is a concrete blocker such as missing access, unacceptable risk, unsupported systems, policy constraints, missing essential inputs, or observed unreliability. Name the blocker.

For **Test** items, prefer a bounded shadow trial on the next natural occurrence of the workflow. Use the real inputs through the user's authorized tools, but default to draft/read-only/sandboxed behavior. Do not send, submit, purchase, delete, merge, deploy, or make another consequential change without explicit user authorization. Measure success/failure, human intervention, elapsed time, cost when available, errors, and approval needs.

Do not propose automatic historical replay as if OpenWorkGraph contains the original task payloads. It deliberately does not capture ordinary typed text, clipboard contents, email bodies, spreadsheet cell contents, passwords, or every application payload, so past observations often cannot reproduce the original inputs faithfully.

Observed agent runs can provide evidence of what agents actually managed to do. Missing agent signals mean **not observed**, not **unavailable**. Keep automation judgments and trial conclusions derived and disposable; they must not become equivalent to canonical captured evidence.

Return a useful reconstruction containing:
- coherent tasks with start/end times, project, purpose, steps, tools, outcome and confidence;
- repeated workflow families and concrete observed instances;
- information handoffs between tools, especially copy/cut/paste or repeated switching;
- friction, unnecessary checking/switching, waiting, and manual coordination;
- automation opportunities grounded in the observed workflow, including the classification above, required current capabilities, what can be automated, what still needs human judgment, and concrete blockers/uncertainties;
- for **Test** items, a safe shadow-trial plan and what should be measured;
- explicit uncertainties or missing signals that would materially change the conclusion.

When raw evidence is absent, say so and work from the richest available captured layer instead.
