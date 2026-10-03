# OpenWorkGraph changelog

Historical release notes consolidated from the former per-version files. New release notes should be added here rather than creating another changelog file.

<!-- Source: docs/CHANGELOG_V035.md -->
# OpenWorkGraph v0.35.0

This release adds non-destructive OWNER identity masking, source-aware Gmail/Outlook person masking, hashed learned-person aliases for later presentation, and restores the self-contained macOS launcher packaging validated by testers.

---

<!-- Source: docs/CHANGELOG_V087.md -->
# OpenWorkGraph v0.87 MCP surface changes

v0.87 adds a compact MCP surface for **new** local AI connections while retaining the previous 24-tool stdio server for backward compatibility.

## New compact surface

```text
get_current_work_context
search_work
get_workflow_trace
get_work_profile
find_repeated_workflows
get_task_context
how_did_similar_runs_go
get_agent_runs
```

When `OWG_EXPERIMENTAL_GOVERNANCE=1`, the compact server additionally registers:

```text
get_action_policy_advisory
get_governed_context_pack
```

## Consolidation map

| Compact tool | Legacy capabilities represented |
|---|---|
| `get_current_work_context` | `get_current_work_context` + `recent_semantic_activity` |
| `search_work` | `search_work_history` + `search_work_observations` + `find_similar_work` |
| `get_workflow_trace` | `get_workflow_trace` + session filtering previously exposed through `get_context_session` / `get_work_session` |
| `get_work_profile` | unchanged |
| `find_repeated_workflows` | `candidate_task_executions` + `automation_candidates` + `find_process_examples` |
| `get_task_context` | unchanged task-context semantics and MCP representation provenance |
| `how_did_similar_runs_go` | `get_similar_runs` + `get_failure_patterns` + `get_next_likely_steps` + observational `get_approval_patterns` + `get_procedural_context_pack` |
| `get_agent_runs` | `get_agent_runs` + `get_agent_execution_trace` via optional `execution_id` |

`company_workflow_summary` is not exposed as a separate compact tool because it is a local current-run summary rather than a Gateway/company-specific capability. Current context and derived workflow views cover that use case without adding another overlapping tool choice.

## Compatibility

Existing configurations that launch:

```text
python -m mcp_server.secure_stdio
```

continue to expose the legacy 24-tool surface. No legacy MCP implementation is deleted or renamed.

New dashboard-generated configurations launch `mcp_server.compact_stdio`. The v0.87 Claude MCP bundle uses the same compact entrypoint, and the on-demand authenticated HTTP bridge uses `mcp_server.compact_http_app`.

## Interpretation boundaries

The consolidation does not change the evidence model:

- observed workflow repetition is not organizational policy;
- approval-request hotspots are observations, not permissions;
- human completion is not automatically a validated success;
- agent coverage remains partial when signals were not observed;
- prompts, model responses, tool arguments/results and hidden reasoning remain outside the agent evidence contract;
- MCP results continue to pass through the existing AI-access checks, audit path and prompt-injection protection.

---

<!-- Source: docs/CHANGELOG_V0871.md -->
# OpenWorkGraph v0.87.1

Bugfix release for the compact MCP feedback loop introduced in v0.87.0.

- `find_repeated_workflows` now exposes exact procedural-memory `family_key` values alongside display `task_family` values when an exact mapping exists.
- `how_did_similar_runs_go` accepts exact family keys and bare canonical human task families, resolves only unambiguous current human families when no key is supplied, and returns explicit `family_selection_required` / `unknown_family_key` states instead of silently empty history.
- Agent family identifiers are never fabricated from bare task-family strings; exact procedural-memory keys remain authoritative for lookup identity.
- Compact `get_current_work_context` and `get_workflow_trace` use smaller defaults while retaining explicit larger limits and stable pagination.
- `get_task_context` and feedback-tool descriptions now explain the expected inputs and discovery chain.
- `docs/EXPORTS_AND_AI.md` now points new local clients to `mcp_server.compact_stdio`; `mcp_server.secure_stdio` remains the legacy 24-tool compatibility entrypoint.

No capture schema, stored evidence, procedural-memory REST route, governance REST route, Gateway transport, or legacy MCP tool is removed by this patch.

---

<!-- Source: docs/CHANGELOG_V0872.md -->
# OpenWorkGraph v0.87.2

This patch makes compact procedural feedback useful to an AI client without changing the stable procedural identity layer introduced earlier.

## Readable human feedback

`how_did_similar_runs_go` can now present human procedures with privacy-safe semantic steps such as:

```text
Gmail · Open email
Salesforce · Open account
Google Sheets · Update status
Gmail · Send
```

These labels are derived from the existing allowlisted/pseudonymized work-surface vocabulary and safe action labels. Arbitrary names, emails, URLs, tenant hosts, paths and free text are not copied into the readable procedure.

## Stable identities remain unchanged

Readable steps are a presentation and matching layer only. Existing procedural-memory structural steps such as `surface:gmail` and `action:click` continue to define structural family identities. v0.87.2 does not migrate, rewrite or reinterpret existing `family_key` values.

Legacy structural step input remains supported. Human-readable progress is accepted only when it exactly matches a privacy-safe semantic step already observed for that family; unknown readable steps return the valid observed step names rather than being guessed.

## Smaller compact context

`get_current_work_context` now returns a summarized first-pass trace instead of embedding full rich event metadata. `get_workflow_trace` remains the canonical evidence tool and keeps the full row contract, but its default page is smaller for agent context budgets. Callers can still request larger pages and follow `next_cursor`.

## Authority boundary

All prior-run feedback remains derived, non-authoritative and non-prescriptive. Repeated behavior is not policy, permission, proof of correctness or an instruction to repeat the procedure.

---

<!-- Source: docs/CHANGELOG_V092.md -->
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

---

<!-- Source: docs/CHANGELOG_V094.md -->
# OpenWorkGraph v0.94.0

## History and retention

- Adds explicit local retention policy for human and agent evidence with separate modes: ephemeral, N days, or forever.
- New installs default to ephemeral until onboarding is completed. Existing installs preserve already-retained history on upgrade rather than deleting it silently.
- Adds a History view/API for browsing retained human sessions and agent executions, deleting one session, and exporting a selected retained time range.
- Session deletion and expiry install durable session tombstones before cleanup so buffered/retried events cannot recreate deleted history.
- Context Pulse cursors are invalidated when retained history or saved-history access changes.

## Saved-history AI access

- Retention and AI disclosure are separate controls.
- Saved history requires an explicit time-limited AI history lease (`selected_range` or `all_saved`). Current-session context continues to work without a saved-history lease.
- The MCP transport centrally intersects historical reads with the allowed date range so older tools cannot bypass the lease.
- Adds `list_history` for metadata-first navigation without eagerly loading full historical evidence.

## Universal structural agent observation

- Adds browser-surface lifecycle adapters for ChatGPT, Claude web, Microsoft Copilot, Lovable, and Gemini.
- Browser-observed agent runs are projected into the existing vendor-neutral agent evidence contract with `observation_level=os_observed`.
- The adapters emit structural lifecycle only: run started/finished/cancelled, approval requested/received, and visible error state.
- They do not capture prompts, model responses, DOM content, tool arguments/results, typed text, clipboard contents, filenames/file contents, or hidden reasoning.
- No new browser permissions are required.

## Versioning

- Root package version: `0.94.0`.
- Claude MCP bundle version: `0.94.0`.
- Browser sensor version: `1.12.0-v94-agent-lifecycle`.

---

<!-- Source: docs/CHANGELOG_V095.md -->
# OpenWorkGraph v0.95.0

## Custom agents and harnesses

OpenWorkGraph now exposes a first-class, framework-neutral path for arbitrary agent runtimes. A self-built harness, an OpenClaw/Hermes-style setup, an internal company agent, or another framework can participate without OpenWorkGraph needing a named native adapter.

The connection is deliberately two-way and independent:

- **agent -> OpenWorkGraph:** privacy-safe structural execution telemetry through the existing write-only agent-ingest boundary;
- **OpenWorkGraph -> agent:** optional authorized context over the compact local MCP surface.

Giving a harness the telemetry token never gives it context/history read access. Giving a harness MCP context never silently enables observation of its execution.

## Standalone helpers

v0.95 adds:

- a dependency-free Python helper (`openworkgraph-agent`) with run/model/tool context managers;
- a dependency-free Node 18+/TypeScript helper with declarations;
- existing OTLP/HTTP JSON integration for harnesses that already emit portable GenAI traces;
- raw structural HTTP for any other language/runtime.

The GitHub release publishes the Python and Node helper files as standalone assets in addition to the normal macOS, Windows and Claude MCP packages.

## Privacy boundary

The custom helpers intentionally have no API for prompt text, model-response content, tool arguments/results, returned values, exception text or hidden reasoning. Tests verify that returned secrets and exception messages remain in the harness and never enter telemetry.

Observation failures remain fail-open for the agent: bounded background delivery can drop telemetry, but it does not enter the agent's control path.

## Reliability

- Python telemetry admission and shutdown share a synchronization boundary, preventing events from being accepted after the worker has closed.
- Node shutdown waits for an already in-flight telemetry request before returning.
- The local dashboard setup endpoint is authenticated and non-cacheable.
- The generated setup keeps write-only telemetry credentials separate from MCP/history permissions.

See [Custom agent harnesses](CUSTOM_HARNESSES.md) for the integration model and examples.

---

<!-- Source: docs/CHANGELOG_V096.md -->
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

---

<!-- Source: docs/CHANGELOG_V097.md -->
# OpenWorkGraph v0.97 — Gateway identity and employee transparency

v0.97 adds an enterprise identity layer to the optional customer-controlled Gateway while preserving the local-first evidence, v0.96 first-value activation, retention, MCP, agent-ingest and export behavior.

## Named Gateway administrators

- `/admin` supports named **owner**, **admin** and **viewer** accounts instead of requiring every administrator to act anonymously through one shared token.
- Administrators can sign in with password + authenticator code (TOTP), or with company OpenID Connect when configured.
- The first owner is bootstrapped with `OWG_GATEWAY_ADMIN_TOKEN`; subsequent administrators get single-use setup links.
- Sign-in sessions have idle and absolute expiry, repeated failures are throttled/locked, and TOTP time steps cannot be reused.
- The bootstrap token can be disabled for normal admin API use after named accounts exist. If an installation loses its only owner's credentials, deliberately re-enabling/presenting the bootstrap token can reset that owner without weakening the normal last-owner protection.
- Audit rows attribute actions to the named administrator when one is signed in.

## Employee roster and identity-bound enrollment

- Administrators can maintain an employee roster manually or by CSV, including team membership.
- Personal invitations lock enrollment to one roster employee; the computer cannot substitute another actor identity.
- A computer records how its identity was established: SSO-confirmed, personal invitation, linked by an administrator, or self-reported legacy/group enrollment.
- Organizations can require identity-bound personal invitations for all new computers.
- Offboarding revokes the employee's active device credentials and open invitations without silently deleting retained evidence.
- When SSO is required, one successful company-account confirmation authorizes **one** computer enrollment. A multi-device invitation requires a fresh confirmation for each additional computer, so a copied invite cannot reuse an earlier SSO proof.

## Employee `/me` view

- An enrolled employee can open `/me` from their own local OpenWorkGraph using a single-use, two-minute login code minted with the device credential.
- When company sign-in is enabled, `/me` may also use the roster-matching company account.
- The page is scoped to that employee and shows their connected computers, evidence held by the Gateway, organization sharing ceiling, readers with explicit access and recorded reads/changes involving them.
- Viewing evidence through `/me` is itself audited.
- If the same email belongs to more than one organization on a multi-tenant Gateway, generic SSO sign-in refuses to guess; the employee must open `/me` from an enrolled computer (or otherwise provide an organization-specific context).

## Company sign-in

- OpenID Connect authorization-code flow with PKCE (S256), nonce, single-use state, issuer/audience/expiry validation and provider-published signing keys.
- If the provider explicitly sends `email_verified: false`, sign-in is refused. Some enterprise providers omit that optional claim; in that case OWG still requires a cryptographically verified ID token, a usable email/UPN claim, optional allowed-domain match, and a matching known administrator/roster employee for the requested action.
- Optional `OWG_GATEWAY_SSO_ALLOWED_DOMAINS` can restrict accepted company-account domains.
- `OWG_GATEWAY_PUBLIC_URL` supplies the HTTPS redirect origin; Docker/self-host configuration passes the identity settings explicitly.

## Security and compatibility

- Setup/session/invitation secrets used by browser pages travel in URL fragments and are removed from the address bar on arrival; Gateway pages use no external scripts and use CSP/nonces, `no-store` and frame denial.
- Existing admin API endpoints remain available to the bootstrap token until the operator disables that path.
- Existing group invitations, managed enrollment, legacy enrollment and already-enrolled devices continue to work. Their identity is shown as self-reported until linked where appropriate.
- Existing v0.96 first-value activation, History/Retention, MCP contracts, agent telemetry, organization sharing ceilings and canonical evidence formats are preserved.
- No SCIM provisioning is included in this release.

---

<!-- Source: docs/CHANGELOG_V098.md -->
# OpenWorkGraph (unreleased, planned v0.98): capture correctness

Measured on a real install, OpenWorkGraph's evidence had four problems:
- Idle time was recorded as focus time. 198 h of foreground time had 12.3 h with any input, and single spans ran overnight.
- Two collectors sometimes recorded at once. 8% of focus time was duplicated, and some days showed more than 24 h.
- About 12% of all events were "capture health" warnings caused by routine checkpoints.
- Sensors reported "ON" on macOS while the OS was withholding their input.

This release fixes those without changing what a field means, what is shared, or the event schema.

## Time you were away is no longer focus time
- **When a span ends:** a focus span ends after 5 minutes (`away_after_seconds`, minimum 60) with no keyboard or mouse input anywhere, or when the screen locks. The time until input returns becomes an **away span** (`event_type: "away_span"`, app "Away").
- **Reading time kept:** the first 5 minutes stay with the app, which covers reading and thinking without input.
- **Meanings unchanged:** `duration_seconds` still means wall-clock observed time. Effort is still `activity.engaged_seconds` / `idle_seconds`.
- **Where idle comes from:** the operating system's input clock on macOS and Windows. It needs no Input Monitoring permission and never sees keys. OpenWorkGraph's own sensors are a fallback, used only when they can actually see input.
- **No idle signal:** without one (Linux today) there is no away detection, instead of guessed absences.
- **Stays on the computer:** away spans are never sent to an organization Gateway, whatever its policy. Sharing them would make OpenWorkGraph presence monitoring.

## One collector per data folder
- **The lock:** the collector holds an OS advisory lock on its data folder for its lifetime. The OS releases it on exit or crash, so there is no stale PID file.
- **A second collector:** another one for the same folder (an orphan from an earlier launch, or a direct launch) exits with code 75 and records nothing. The supervisor checks again every 15 s instead of respawning every second.
- **Separate folders:** `data/live` and `data/demo` are separate.

## Document changes within one app
- **The new default:** `change_detection: "application_and_document"`. When the window title changes materially within the same app and stays changed for 2 polls, the span ends. The new span starts when the new document first appeared.
- **What counts as noise:** unread badges, unsaved markers, "Edited"/"Redigerad", progress percentages and "Not Responding" are ignored, as are one-poll dialogs and titles going empty.
- **Title privacy modes:**
  - `none`: nothing about titles is used.
  - `hash`: normalization happens in memory before hashing, and nothing new is stored.
  - Excluded apps: never tracked.
- **Existing configs:** `"application"` was the shipped default copied into every `config.json`, so it now follows the default. `"application_only"` keeps the old behaviour. `"application_and_title"` is unchanged.

## Capture health means degradation again
- **What triggers a warning:** only real degradation produces `capture_health` evidence, namely dropped interactions, worker errors, an unreadable foreground window, or missing permissions. A new kind of problem or a permission change is reported immediately; repeats at most every 10 minutes.
- **Routine counters:** checkpoints, capture gaps, document boundaries and away spans go to `diagnostics` in the heartbeat and event instead.

## macOS permissions are stated, not assumed
- **How it checks:** the collector asks macOS directly with `AXIsProcessTrusted` and `IOHIDCheckAccess`, which never prompt, and rechecks every minute.
- **What it reports:** it names each sensor that is BLOCKED, and why. Clicks and titles need Accessibility; keyboard counts and copy/paste shortcuts need Accessibility and Input Monitoring.
- **Heartbeat:** `keyboard_sensor` is false when keys cannot arrive, and the permissions are included.
- **Dashboard:** the Recording pill shows "macOS … permission missing". Missing permissions are recorded as capture health.

## Claude Code turns
- **New hooks:** `UserPromptSubmit` starts a turn and `Stop` finishes it successfully, keyed by `prompt_id`, which Claude's tool hooks and OTel share. Sessions keep their own `SessionStart`/`SessionEnd` boundary.
- **Missing `prompt_id`:** those turn hooks are ignored rather than mistaken for the session.
- **Content never read:** these hooks carry the prompt and the last reply, and the adapter reads neither.
- **Existing installs:** OpenWorkGraph adds missing hook events at startup when Claude Code Observe is on, with a backup. Your own hooks and env are kept, and hooks are never installed where Observe was not turned on.

## Agent telemetry diagnostics
- **Where:** `GET /v1/agent-telemetry/diagnostics`, and a line in each Connections row.
- **What it shows:** per channel (Claude hooks, Claude OTel logs, Codex OTel, other agent events, generic OTel, spool), since OpenWorkGraph started:
  - when requests arrived;
  - rejections by reason;
  - OTel records seen and ignored;
  - events stored.

  It also shows which exporters and hook events are configured.
- **No content:** counts, timestamps and reason codes only.

## Delayed delivery for agent hooks, only while recording
- **When it spools:** if a hook cannot reach OpenWorkGraph, the event may wait in a local spool (`data/auth/agent_spool`) under a 90-second recording lease. Only a running, recording, non-demo OpenWorkGraph grants the lease, and it is revoked on Pause, Stop and exit.
- **After Stop or quit:** agent events are dropped, not collected for later.
- **On delivery:** spooled events go through the normal ingest rules again (Observe switches, deletions, retention, pause windows).
- **Limits:** 2,000 files of 256 KB, 24 h maximum age. A rejection is never spooled.

## Not in this release
- Clipboard-write detection.
- Copilot, Gemini CLI and Cursor presets.
- Multilingual web-agent detection.
- Claude metrics export.
- `PreCompact` (it needs a schema operation that older Gateways would reject).
- Platform rework: macOS without `osascript`, Windows UWP app names, Linux/Wayland.
- **Known issue, unchanged:** Claude subagent runs share their parent turn's `run_id`, so they are grouped into the parent turn rather than linked as children.

---

<!-- Source: docs/CHANGELOG_V099.md -->
# OpenWorkGraph (unreleased, planned v0.99): capture coverage

Builds on v0.98's capture correctness. It widens what OpenWorkGraph can see without changing the privacy model: structure only, no content, the same Observe switches.

## Observe GitHub Copilot, Gemini CLI and Cursor
The Observe switch now works for three more apps. As with Claude Code and Codex, it backs up the file, keeps your settings, refuses rather than overwrite your own telemetry, and Remove takes out only what OpenWorkGraph added.

- **GitHub Copilot in VS Code:**
  - **How:** Copilot's own OpenTelemetry export, with content capture explicitly off.
  - **What is recorded:** agent runs, model calls (model, tokens) and tool calls from Copilot's GenAI spans. Tool arguments and results are never read.
  - **When it applies:** after VS Code reloads its window.
- **Gemini CLI:**
  - **How:** OTLP over HTTP with `logPrompts` forced **false**. Gemini's default is true, which puts prompts and tool arguments in its log events.
  - **What is recorded:** turn starts, model calls, tool calls, and human accept/reject decisions (auto-accept is not a person).
  - **Allowlist:** nothing else is read even if prompt logging is re-enabled.
- **Cursor:**
  - **How:** hooks for sessions, turns (`beforeSubmitPrompt` → `stop`, one per generation), tool results and subagent outcomes.
  - **Never blocks:** Cursor runs hooks synchronously, so the hook answers first with a non-blocking reply. It never uses a permission hook, so it cannot approve or deny anything.
- **Authentication:**
  - Copilot and Gemini cannot send an Authorization header from a settings file, so their OTLP endpoint path carries a separate write-only token.
  - The access log shows it as `[redacted]`.
  - Only OTLP/HTTP JSON is accepted; protobuf gets a clear 415.
- **Diagnostics:** each new app has its own line (Copilot OTel, Gemini OTel, Cursor hooks) in Connections.
- **Agents tab:** the "past runs hidden" note now names only apps whose runs were actually hidden.

## Clipboard writes
- **What is new:** copies made without a shortcut (menu, right-click, drag, an app) are now noticed from the OS clipboard change counter, as `clipboard_write`. The counter never exposes contents.
- **No double counting:** a Cmd/Ctrl+C that already produced `clipboard_copy` is not counted twice.
- **Paste linking:** a later paste links to whichever write came last.
- **What it never does:** a write is never turned into a paste, since pastes do not change the counter; pastes are still shortcut-only. Nothing is attributed while you are away.
- **Opting out:** `clipboard_write_detection_enabled: false`.
- **Timing fix:** click, scroll and clipboard events are now stamped with the time they happened, not when the worker thread processed them.

## Web agents in any UI language
The browser sensor recognises ChatGPT, Claude, Gemini, Microsoft Copilot and Lovable runs from structure first:
- `aria-busy` and streaming markers;
- stable `data-testid` tokens for send and stop;
- submit buttons in the composer's form;
- Enter in the message box (not Shift+Enter or IME composition).

The English button labels are only a fallback, so a Swedish or German UI works whenever the site exposes any of these. The browser sensor version is 1.13.0; reload the extension to update.

## Not in this release
- Claude Code metrics export.
- Copilot CLI (environment-variable configuration only).
- Platform rework (macOS without `osascript`, Windows UWP app names, Linux/Wayland).

---

<!-- Source: docs/CHANGELOG_V0100.md -->
# OpenWorkGraph v0.100.0: richer agent work evidence

Builds on v0.99. Agent runs now say what the agent actually did, not only which tools it called. The privacy model is unchanged: structure only, no content.

## What each tool call did (content-free)
- **Derived in memory, per tool call:** Claude Code and Cursor hooks and Codex's log events carry a tool's input and output. From them OpenWorkGraph now derives a few structural facts, and nothing else:
  - which well-known programs ran (`pytest`, `git`, `npm`), from a fixed allowlist;
  - git and GitHub CLI operations (`commit`, `push`, `pr_create`);
  - test pass/fail counts from a recognised runner summary (pytest, jest, vitest, cargo, node --test, unittest); a later observed test command with no recognisable summary is reported as `unknown`, not as an earlier passing result;
  - file types, keyed hashes of file paths, and lines added/removed when those counts are available.
- **What is never stored:** command text, arguments, paths, file names, contents and output.
- **One gate:** every value passes one allowlist gate again when it is stored. Quoted text, such as a commit message or an `echo` string, never counts as a command. Malformed quoting fails conservative and does not split later separators into invented commands.
- **File refs:** keyed with a secret that never leaves the computer. They show "the same file again" and cannot be compared across devices.
- **Off switch:** `OWG_AGENT_TOOL_DETAIL=0`.

## Run work summary
- **What it holds:** each agent run in the traces (and MCP `get_agent_runs`) has a `work_summary`:
  - commands used;
  - git/gh operations;
  - test runs, and whether the latest observed test result is passing, failing or unknown;
  - files edited vs only read (using the structural tool identity when an edit hook has no line-count patch);
  - file types, lines changed when available, and total tokens.
- **Links:** run lists also carry `parent_execution_id` / `child_execution_ids`. The compact MCP view carries `usage_totals` and `models_observed`.

## Codex token usage
- **Before:** Codex reports tokens on `response.completed` SSE events, not on API requests, so Codex runs showed no token usage.
- **Now:** each completed response is one model call with input, output, cached and total tokens. A successful API request no longer adds a second, empty model call. HTTP/API failures and Codex `response.failed` SSE events remain error model calls.
- **Duplicate copies:** Codex sends each event as both a log record and a trace span event, and only the log copy has the tool's arguments and output. Whichever copy is stored first, the later one may fill in missing tool detail or token usage. It never overwrites a stored value.

## Claude Code subagents are their own runs
- **Before:** a subagent's tool calls merged into the parent turn.
- **Now:** hooks fired inside a subagent (they carry `agent_id`) form a child run. SubagentStart/SubagentStop open and close it, and the handoff links it to the parent turn.
- **Stable IDs:** the child run ID uses only the session and the subagent ID, so it does not depend on the subagent's hooks carrying the parent's `prompt_id`.
- **Ephemeral history:** a subagent finishing does not purge the session.

## Deliberately not in this release
- **Claude Code OTel metrics** (commits, PRs, lines of code): commits, PRs and lines are now derived structurally for every hook-based agent. A second, Claude-only metrics feed would count the same work twice.
- **PreCompact:** still not a run boundary.

---

<!-- Source: docs/CHANGELOG_V0101.md -->
# OpenWorkGraph v0.107 stack slice: run memory

This change is an unreleased slice of the v0.107 stack, built on v0.100. Ephemeral raw history otherwise leaves repeated-workflow and similar-run features nothing to learn from after a session ends; run memory preserves a small structural summary without keeping work content.

## Run memory
- **What is kept:** one small, content-free record per run, taken just before retention removes the session (ephemeral close, expiry or crash recovery):
  - hashed family and structural steps;
  - the agent's reported end status and timing;
  - approval points;
  - for agents, the work summary: commands, git/gh, test outcome, file and line counts, tokens.
- **What is never kept:** titles, URLs, paths, prompts or tool content. Native session/run IDs are never stored in plaintext; persistent linkage uses local-key HMACs, while public execution references remain opaque one-way hashes.
- **New-install default:** on for 90 days, disclosed in History and independently switchable/deletable.
- **Upgrade behavior:** an existing policy created before run memory where both human and agent history were explicitly `ephemeral` migrates with run memory **off**, so an old "don't keep after session" choice is not silently widened. Existing users already retaining some history keep the disclosed run-memory default.
- **Deletion wins:** deleting a session or a date range yourself deletes its memory too. A run that exists only in memory can be deleted from History by its execution ID.
- **Used by:** `find_repeated_workflows`, `how_did_similar_runs_go` and the procedural-memory API, with `"source": "run_memory"`. A run still in raw history is always derived from the raw evidence, never duplicated.
- **Local only:** memory never leaves this computer (the Gateway connector syncs only events). MCP reads it only under "All saved history" AI access.

## API
- `GET /v1/run-memory`, `PUT /v1/run-memory/policy` (`enabled`, `days`), `POST /v1/run-memory/forget` (`execution_id` or `all`).
- `GET /v1/history-policy` includes `run_memory` and explains the tradeoff.
- Session deletion, range deletion and cleanup results report `run_memory_kept`, `run_memory_deleted` and `run_memory_pruned`.

This slice does not publish a separate v0.101 release; the completed stack publishes as v0.107.0.

---

<!-- Source: docs/CHANGELOG_V0102.md -->
# OpenWorkGraph v0.107 stack slice: did the work hold up?

This unreleased slice builds on run memory. Until now a run's outcome was the agent's own end status. Claude's Stop, for example, means the turn ended, not that the work was right. Outcome tracking adds what happened to delivered pull-request work.

## Outcome tracking (off by default)
- **Turning it on:** the History tab card "Did agent work hold up?".
- **What is watched:** pull requests an agent opens, via `gh pr create` or a create-pull-request tool, from Claude Code, Cursor or Codex.
  - Kept: only `github.com`, owner, repository and number, in a local watch list keyed to the run.
  - Never kept in events, never synced.
  - Untrusted tool output cannot turn an arbitrary syntactically valid hostname into a polling target; only `github.com` is accepted today. Future GHES support should use explicit trusted-host configuration.
- **When the watch starts:** the watch is timestamped from the event's actual `observed_at`, not delayed spool/ingest time, so the 30-day horizon and user date-range deletion follow the work event.
- **What is checked:** every 10 minutes, merged/closed state and CI, through your local `gh` login (read-only), with an argv list, no shell and a timeout.
- **What the run gets:** a content-free `delivery_outcome`: PRs opened, merged, closed unmerged, open, and CI passing/pending/failing.
- **Where it shows:**
  - agent traces and MCP `get_agent_runs`;
  - run memory, updated even after the session's raw history is gone;
  - procedural-memory family summaries;
  - the History list.
- **When links go:** a link is dropped when the PR resolves or after 30 days. Turning tracking off deletes every stored live link, and deleting a session or date range deletes its watches.
- **Errors:** stored as codes only. A missing or logged-out `gh` is shown in the card.

## API
- `GET /v1/outcome-tracking`, `PUT /v1/outcome-tracking` (`enabled`), `POST /v1/outcome-tracking/check-now`.

## Not yet
- Reverts of merged work.
- Commits pushed without a pull request.
- Human rework after a run: that comes with the human↔agent join.

This slice does not publish a separate v0.102 release; the completed stack publishes as v0.107.0.

---

<!-- Source: docs/CHANGELOG_V0103.md -->
# OpenWorkGraph v0.107 stack slice: session-start briefs

This unreleased slice closes the learning loop: an opted-in Claude Code session can start with a short, content-free description of how prior structural work went.

## Brief agents at session start (off by default)
- **Turning it on:** the History tab, "Brief agents at session start" → Claude Code. This adds one synchronous `SessionStart` hook, separate from the observation hooks. Turning it off removes only that hook.
- **When it runs:** new, cleared and compacted sessions (not resumed ones).
- **Scope:** prior runs in the same keyed project when available; if the current project has no history yet, the brief clearly labels a cross-project fallback over the past 30 days.
- **What the brief holds:** test outcomes, pull-request outcomes when available, allowlisted commands, typical files/lines and median tokens.
- **Honest unknowns:** if the latest test tool use has no recognized test summary, the brief says the result is unknown and does not invent `0 passed, 0 failed`.
- **Where the facts come from:** raw history and run memory. It works with "Don't keep after session" when run memory is enabled.
- **What it never holds:** titles, paths, prompts or tool content. It is at most 900 characters and labeled observational, not instructions.
- **Preview:** "Preview brief" shows exactly what an agent would receive. The card counts every brief actually sent.
- **Token:** the hook uses its own brief-only token. The write-only ingest token still cannot read anything.
- **Failure:** the hook fails open. After 2 seconds or on any error it adds nothing.

## Project identity
- Claude Code and Cursor events carry `workspace_ref`, a keyed hash of the working directory, so "same project" is known without storing a path.
- It appears on traces, procedural executions and run memory.

## API
- `GET/PUT /v1/agent-brief`, `GET /v1/agent-brief/preview`, `POST /agent-brief/v1/brief` (brief token only).

This slice does not publish a separate v0.103 release; the completed stack publishes as v0.107.0.

---

<!-- Source: docs/CHANGELOG_V0104.md -->
# OpenWorkGraph v0.107 stack slice: human work joined to agent runs

This unreleased slice builds on the session-brief stack. Human capture and agent observation were recorded side by side but never connected; each agent run can now describe what the person did around it without adding content capture.

## What the person did during and after a run
- **Measures:** traces, run memory and MCP run lists gain `human_context`:
  - `during` the run: engaged and away seconds, by app category (terminal, editor, browser, communication, AI assistant, other);
  - `after` it: the same measures until the next turn in the session (at most 2 hours), plus the gap.
- **No capture is not zero:** without human capture in a window, it says `human_capture_observed: false`.
- **Subagents:** runs inside a turn are not treated as the "next turn".

## Did the person rework the immediately preceding agent turn?
- **How it works:** the Claude Code hook tracks file refs edited in the current turn, snapshots the project's changed files as keyed hashes of paths and contents when that turn ends, and compares at the next prompt.
- `between_turns.agent_files_changed` counts only changed files the **immediately preceding turn** edited. Older edits are not carried forward through a rolling 24-hour set, avoiding false attribution to a later turn.
- The next turn records only counts: files changed, immediately-prior-turn overlap, whether HEAD moved, and the gap.
- **Where it shows:** it is attributed to the run it followed (`human_context.after.between_turns`), shown in History and summarized in session briefs.
- **Cross-platform correctness:** concurrent async Claude hooks are serialized with an atomic lock file that works on Windows, macOS and Linux. Lock ownership is tokenized, stale locks recover, and cleanup never removes another process's active lock.
- **Safety:**
  - runs only under a recording lease;
  - git's fsmonitor is disabled, so a repository cannot make it run commands;
  - it never takes git's index lock;
  - git calls have a 2 s timeout;
  - snapshots are bounded to 200 files and 50 MB;
  - state is local, keyed by the project's hash, and expires after 7 days.
- **Off switch:** `OWG_AGENT_REWORK=0`.

## Verification
The regression suite includes a real temporary git repository, fsmonitor trap, symlink handling, sanitization, human-context joining, previous-turn-only attribution and a separate Python process that holds the workspace lock so Windows/macOS/Linux CI exercise cross-process contention.

This slice does not publish a separate v0.104 release; the completed stack publishes as v0.107.0.

---

<!-- Source: docs/CHANGELOG_V0105.md -->
# OpenWorkGraph v0.107 stack slice: do briefs help?

This unreleased slice measures whether opted-in session briefs help on the person's own work, without pretending historical ordinary briefs were randomized.

## Measure whether briefs help (off by default)
- **Turning it on:** the History tab, "Brief agents at session start" → "Measure whether briefs help".
- **The holdout:** a random 1 in 5 brief-eligible sessions are held back. Assignment uses `secrets.randbelow`, is stored explicitly in SQLite, and is sticky for `(trial, session)` so `/clear` or compaction stay in the same arm.
- **Trial isolation:** enabling evaluation starts an opaque trial ID. Turning evaluation off and on again starts a new trial. Ordinary briefs delivered before a trial are not treatment observations and never enter that trial's report.
- **What is compared**, per session and only on turns after assignment:
  - turns whose tests ended failing;
  - turns after which the person changed files the immediately preceding agent turn had edited;
  - pull-request merge rate (with outcome tracking);
  - tokens per turn;
  - turns per session.
- **PR right-censoring:** merge rate is `merged / (merged + closed_unmerged)`. Open/pending PRs are unresolved and excluded rather than silently counted as failures to merge.
- **How:**
  - The session is the unit.
  - Each measure shows arm means and the difference (briefed minus control) with a bootstrap 95% interval (2,000 resamples, fixed seed, reproducible).
  - A directional verdict requires the interval to exclude 0 and the metric to have a declared better direction.
- **Honest defaults:**
  - Nothing is claimed until each arm has at least 10 sessions with the measure.
  - "Turns per session" never claims a direction.
  - The method is labeled exploratory: several measures, no multiple-comparison correction.
- **Sources:** raw history and run memory, matched by keyed session refs.

## API
- `GET /v1/agent-brief/evaluation`, `PUT /v1/agent-brief/evaluation` (`enabled`). `GET /v1/agent-brief` includes the evaluation state and active trial metadata.

This slice does not publish a separate v0.105 release; the completed stack publishes as v0.107.0.

---

<!-- Source: docs/CHANGELOG_V0106.md -->
# OpenWorkGraph v0.107 stack slice: playbooks

This unreleased slice makes structural workflow knowledge portable between people, devices and agents without carrying the work itself.

## Playbooks
- **Export:** History → Playbooks → Export turns one repeated workflow (at least 2 runs, from raw history or run memory) into a small JSON file. It holds:
  - canonical readable structural steps;
  - allowlisted commands and git/gh operations;
  - test and pull-request outcomes;
  - how often the person reworked the immediately preceding agent turn, and time to the next prompt;
  - typical files, lines and tokens.
- **What it never holds:** titles, paths, prompts, tool content, device-keyed file/workspace refs, execution IDs or evidence references. Its name is the only free text and must be short, plain and not instruction-like.
- **Import is an untrusted agent-visible boundary:** another person's file goes through one strict gate on import and again on every read.
  - `typical_steps` must match the actual OpenWorkGraph structural grammar (`model_call`, approvals/errors, or canonical `tool:<category>:<safe-label>[:failure]`). A safe character set alone is not accepted, so strings such as `ignore_previous_instructions` or `run_rm_rf` are dropped.
  - Device-local opaque prefixes (`f:`, `w:`, `event:`, `execution:`, `run:`, `s:`) cannot survive portable step validation.
  - Family keys must be actual generated `agent:workflow:<hex>` or `agent:structure:<hex>` values; framework labels are restricted to known structural runtimes.
  - Commands/git/gh operations are allowlisted; numbers/rates are bounded; unknown keys are dropped; total size is capped at 32 KB.
- **Agents:** imported playbooks are available through compact-MCP `get_playbooks`. `include_my_workflows=true` separately requires "All saved history" AI access. The legacy 24-tool server remains frozen.
- **Run memory** keeps readable structural steps and family so playbooks can work after raw ephemeral history is purged.

## API
- `GET /v1/playbooks/local`, `GET /v1/playbooks/export?family_key=&name=`, `POST /v1/playbooks/import`, `GET /v1/playbooks/imported`, `DELETE /v1/playbooks/imported/{id}`.

This slice does not publish a separate v0.106 release; the completed stack publishes as v0.107.0.

---

<!-- Source: docs/CHANGELOG_V0107.md -->
# OpenWorkGraph v0.107.0: agent learning loop and trustworthy setup diagnostics

v0.107.0 is the single public release for the v0.101–v0.107 development stack. Intermediate stack slices remain unreleased.

## Setup checks
Per-channel delivery counts already showed *what* arrived. Each Connect card now also gives a specific next step when observed configuration/delivery evidence shows a problem. `/v1/agent-telemetry/diagnostics` returns `checks`:
- **Claude Code hooks arriving but no model/token telemetry:** this is reported as an observation, not as proof of one cause. A common reason is that open sessions started before telemetry was enabled. **Start a new Claude Code session; if telemetry is still absent, switch Observe off and on and re-check.**
- **Claude Code hooks out of date** (missing turn events). **Restart OpenWorkGraph, then start a new session.**
- **Claude Code telemetry not set up.** **Switch Observe off and on.**
- **Token rejected** on any channel. **Switch Observe off and on** to write the current token.
- **OTLP protobuf** received; only OTLP/HTTP JSON is accepted.
- **No Codex telemetry yet** (informational). Codex reads its settings when a session starts.
- **Agent events waiting** while recording is paused or stopped (informational). They expire after 24 hours.
- **Outcome tracking** on without `gh`, or with `gh` logged out.
- **Session briefs** on, but their Claude Code hook is missing from the settings file.

The recency calculation treats an event received exactly now (`age == 0`) as recent rather than accidentally falling through a truthiness check.

## Full v0.107 stack
- **Run memory:** content-free structural run summaries can survive raw-history retention, while upgrades preserve an existing user's explicit both-streams-ephemeral choice by leaving run memory off until they opt in.
- **Outcome tracking:** opt-in `github.com` PR/CI outcome polling through the local `gh` CLI; arbitrary tool-provided hosts are not network targets, and watch timing follows the observed work event.
- **Session briefs:** opt-in bounded Claude Code context from prior structural runs, with unknown test results kept honestly unknown.
- **Human ↔ agent join:** coarse human activity around runs and immediate previous-turn rework attribution, with cross-platform interprocess locking for concurrent async hooks.
- **Brief evaluation:** isolated randomized trials, sticky assignment per trial/session, historical ordinary briefs excluded, and open PRs right-censored from merge-rate denominators.
- **Playbooks:** portable structural workflow summaries whose imported steps must match canonical OpenWorkGraph grammar; arbitrary prompt-like strings and device-local refs are rejected.
- **Release convergence:** root package, Python SDK, TypeScript SDK, MCPB manifest and version consistency test are all `0.107.0`.

## Merge/release procedure
Merge #106 → #107 → #108 → #109 → #110 → #111 → #112 in order. After each parent lands, retarget the next PR to `main` and require fresh green CI. Only #112 carries the `0.107.0` version bump and should trigger the next public tester release.

---

<!-- Source: docs/CHANGELOG_V0108.md -->
# OpenWorkGraph (unreleased, planned v0.108): full context, no personal details

## Titles keep their context; only personal details become tokens
- **What changed:** before v0.108, browser tab titles were cut to the site name before storage. `Re: Contract renewal Q4 - Anna Svensson - Gmail` became `Gmail`. Desktop-app titles, meanwhile, were stored with names in them.
- **Now:** every title (browser and desktop alike), control label and URL path keeps its text. These become stable tokens when detected, before anything is stored:
  - people's names;
  - email addresses and phone numbers;
  - personal identity numbers, IBANs, payment cards, credentials and labelled personal IDs.
- **Example:** `Re: Contract renewal Q4 - PERSON_x <EMAIL_y> - Gmail`.
- **Kept:** subjects, documents, projects, company and product names, order and invoice numbers, dates, amounts.
- **Same person, same token:** a name seen next to an email address gets the email-linked token everywhere.
- **Grouping:** browser events keep the recognised work surface (`page.surface`).
- **Upgrade:** evidence stored earlier is protected once, in checkpointed batches of 1,000 rows. Up to 5,000 rows are done during start-up (well under a second); a longer history continues in the background so launch is never delayed. Until it finishes, AI context is served as Redacted even if Full is chosen, and exports are redacted; Full returns automatically afterwards. Only rows where a personal detail is detected are rewritten; about 9,000 rows per second in testing. It is recorded as a privacy migration. Browser titles that older versions already cut down cannot be restored.
- **Fix:** an IBAN written in groups and followed by a word ("SE45 5000 … 7466 payment") was not recognised. It is now. IBANs must also match their country's exact length.
- **Tokens are never re-read:** existing tokens (PERSON_x, EMAIL_x, IBAN_x, …) are shielded from every detector, so protecting text twice changes nothing.
- **Fails closed:** if a privacy pass errors, the title, label or path is dropped rather than stored partly processed.
- **AI context "Full"** now means "titles as stored", which no longer contain names or contact details.

## Export is redacted by default
- **Defaults:** Export now defaults to **Redacted** and to including every captured event. Since v0.108 the difference from before is small: redaction runs a second pass that also covers anything captured before this version. History's JSON export is redacted too.
- **Honest text:** the export text now says what the file keeps (full titles and business context), that detected personal details are tokenized, and that detection is best effort.

## Easier first use
- **Value first:** the "See what OpenWorkGraph understands" reconstruction comes first, and the retention choice ("Keep this beyond today?") sits directly under it. Its text reflects whether history is currently kept or deleted.
- **Connect:**
  - lists apps found on this computer first, with a one-line recommendation;
  - folds the rest under "Other apps";
  - gains a **Brief** column for session briefs, next to Context and Observe.
- **Agents tab:** has a new **Agent learning** section with session briefs (preview and measurement), outcome tracking and playbooks. **History** keeps only retention, run memory, saved-history AI access and saved sessions.
- **Friendly labels:** the first-value reconstruction uses the same surface names as the rest of the dashboard (Gmail, not mail.google).
- **Branding:** the macOS launcher and console say OpenWorkGraph instead of Workflow Observer. Install folders are unchanged, so upgrades keep working.
- **Install steps:** README, README_FIRST and the testing guide give accurate macOS 15+ Gatekeeper ("Open Anyway" in Privacy & Security) and Windows SmartScreen ("More info → Run anyway") steps.
- **Testing guide:** `NONTECHNICAL_TESTING.md` is rewritten for the current dashboard (it still described v0.57).

---

