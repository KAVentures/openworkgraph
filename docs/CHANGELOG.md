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

