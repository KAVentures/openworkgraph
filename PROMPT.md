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

Return a useful reconstruction containing:
- coherent tasks with start/end times, project, purpose, steps, tools, outcome and confidence;
- repeated workflow families and concrete observed instances;
- information handoffs between tools, especially copy/cut/paste or repeated switching;
- friction, unnecessary checking/switching, waiting, and manual coordination;
- automation opportunities grounded in the observed workflow, including what can be automated and what still needs human judgment;
- explicit uncertainties or missing signals that would materially change the conclusion.

When raw evidence is absent, say so and work from the richest available captured layer instead.
