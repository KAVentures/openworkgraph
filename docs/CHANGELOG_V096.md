# OpenWorkGraph v0.96 — first useful reconstruction

v0.96 adds a thin first-run activation layer over the existing local evidence stack. It is intended to make OpenWorkGraph understandable during the first real work session without changing what the product captures or what an AI may access.

## What changes

- Overview shows a dismissible **See what OpenWorkGraph understands** card.
- Progress is evidence-driven rather than a countdown: observed events, work surfaces, transitions and agent runs.
- Once enough current-session evidence exists, **Your last few minutes** presents a deterministic reconstruction from existing privacy-hardened dashboard evidence and structural agent-run reports.
- If interaction-level evidence is sparse, already-derived observed surface transitions provide a factual fallback rather than inventing task intent.
- The next action is contextual: Connect AI only when the user chooses, optional browser-sensor setup when browser context is shallow, and optional native/OTel agent telemetry when an agent is only surface-observed.
- The old unfinished timeline placeholder and an empty repeated-workflows card are suppressed on first run until they have useful content. Their underlying DOM/data paths remain intact.

## What does not change

v0.96 adds no capture sensor, screenshot capture, filesystem watcher, prompt/response capture, clipboard-content capture, browser/OS permission, database schema, retention rule, AI permission, saved-history lease, Gateway behavior, agent-ingest permission, export format or MCP tool.

The first-value layer performs GET requests only against existing authenticated local endpoints. It cannot enable AI access, save history, synchronize evidence or mutate canonical evidence.

## Compatibility goal

Evidence, History, Agents, Connect, Organization and Export remain the established advanced surfaces. Dismissing the first-value card leaves the ordinary dashboard behavior intact. Existing installations and existing MCP configurations keep their prior semantics.
