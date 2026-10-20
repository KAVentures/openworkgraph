# Reviewable AS-IS / TO-BE process design

This optional, local-only designer builds on [CSV process mining](PROCESS_MINING_CSV.md)
and does **not** replace desktop workflow discovery, the existing Evidence
view, or the OWG agent connectors.

## Workflow

1. In **Evidence → Process mining**, select and analyze a small, authorized CSV
   event log.
2. Select an **observed process variant** whose path has not been truncated.
3. Click **Design improved TO-BE workflow**.
4. Compare the unmodified AS-IS path against a separately editable TO-BE path.
   Move steps up/down, remove steps, or add new structural activity labels.
5. Describe the intended business outcome, review the proposal, and tick the
   explicit confirmation.
6. Click **Export reviewed draft as JSON** to save an agent-neutral
   implementation context package locally.

Selecting another source file clears the draft; replacing a draft requires
explicit confirmation. No network calls are introduced by this interface.

## Semantics

- **AS-IS** is an observed ordered event sequence from the user-selected
  source event log. The CSV does not prove completed work, business
  requirements, or source-system authoritativeness.
- **TO-BE** is a new user-edited *proposal*, not inferred company policy or an
  AI-generated optimization. Its steps stay separate from the observed data.
- Multiple occurrences of an activity stay multiple occurrences. The change
  summary uses occurrence counts rather than incorrectly collapsing repeated
  steps; it flags a pure reordering only when both multiset counts agree.
- A goal typed by the user is explicitly marked as a user declaration.
- The export carries no native business case IDs, captured clipboard/text,
  uploaded CSV contents or customer payload fields.
- The export **never grants permission** to execute the steps or contact
  customers. Business decision rules, exceptions, approvals, source-of-truth
  systems, actual outcomes and allowed tools still require separate validation.

## Limits

- The designer requires a complete, structurally labeled observed baseline;
  truncated paths must be explored differently.
- Proposals contain 1–40 steps; labels are 1–64 characters and use the same
  allowlist as the CSV activity schema.
- Exported JSON is a portable draft, not an executable automation, validated
  BPMN, or business-policy record. Nothing is deployed or automatically
  synced to an AI or business system.
- The draft remains in browser memory only and disappears on refresh/reopen;
  use explicit export to keep it.

## Acceptance

Tests cover duplicate activity positions, reorder vs add/remove semantics,
explicit review, incomplete-baseline rejection, JSON export guardrails,
no browser-side network requests, and old CSV explorer compatibility.
Before production use, complete real-browser keyboard/mobile acceptance and
cross-platform CI. The next milestones are editable decision/exception paths,
BPMN validation, signed-off process catalog entries and implementation
templates for authorized agent runtimes.
