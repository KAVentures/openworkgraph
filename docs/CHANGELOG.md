# OpenWorkGraph changelog

# OpenWorkGraph v0.121.0

This release makes continuous desktop observation survive ordinary shutdown/restart cycles and recover from transient observer failures.

- **macOS Start at Login:** on the first v0.121+ launch, the menu-bar app registers through Apple's supported Service Management API. The OWG menu exposes **Start OpenWorkGraph at Login** so the user remains in control.
- **Windows continuity preserved:** the installer continues to enable **Start OpenWorkGraph when I sign in** by default.
- **Bounded crash recovery:** macOS and Windows restart an unexpectedly exited observer with short exponential backoff, but pause recovery after five crashes in five minutes.
- **Intentional Quit is authoritative:** Quit and manual Restart clear process ownership before termination so the supervisor never mistakes a deliberate stop for a crash.
- **No capture-model change:** evidence, privacy controls, retention, local data paths, MCP and Gateway behavior are unchanged.

---

# OpenWorkGraph v0.120.0

This release adds real desktop installers so normal users no longer need a ZIP, Terminal, PowerShell, or a separate Python installation.

- **macOS desktop installer:** `OpenWorkGraph-macOS.pkg` installs the offline menu-bar app into Applications. The signed runtime stays sealed inside the app bundle while mutable source/state use the established `~/Library/Application Support/WorkflowObserver` location; existing `data/` and `config.json` are preserved.
- **Windows desktop installer:** `OpenWorkGraph-Windows-Setup.exe` installs the offline tray app per-user under LocalAppData and creates the normal Start-menu entry. The Inno Setup uninstaller remains available.
- **Stable website downloads:** GitHub Releases publish unversioned installer aliases plus versioned installer artifacts and SHA-256 files.
- **Advanced fallbacks remain:** release-matched shell installers and tester ZIPs stay available for technical users and troubleshooting.
- **Signing-ready:** the signed-installer workflow can replace the stable aliases with notarized macOS and signed Windows builds when signing credentials are configured.
- **No capture behavior change:** collectors, evidence schema, privacy controls, MCP, Gateway, exports and agent integrations are unchanged.

---

# OpenWorkGraph v0.119.0

This release adds conversational background context and makes the existing local-first tester path easier to install and remove without introducing a second runtime or data architecture.

## Conversational background context

- Historical evidence applies both dates before the source limit and reports bounded coverage.
- Repeated-workflow discovery honors current/selected history access; lexical search accepts dates and explains its limits.
- Privacy and History expose optional standing grants with explicit revocation.
- Compact MCP adds portable user-reviewed workflow knowledge with revision checks and deletion; dedicated evidence/knowledge calls enforce master access and audit responses.
- Human Gateway adds optional delegated OAuth Streamable HTTP MCP with personal actor isolation, protected-resource metadata, reviewed knowledge and evidence bundles.
- Microsoft 365 Copilot setup templates and documented ChatGPT connection/restart behavior are included; account/tenant provisioning remains external setup.

## Easier local installation

- **Release-matched bootstrap installers:** `install.sh` and `install.ps1` are GitHub Release assets pinned to the same v0.119 ZIP they install, instead of executing installer code from `main` against a separately moving latest package.
- **Normal OS relaunch points:** macOS gets a per-user Applications launcher and Windows gets a current-user Start-menu shortcut; both delegate to the established stable local installation.
- **End-to-end installer CI:** macOS and Windows CI execute the real bootstrap against the freshly built ZIP, verify local `/health`, relaunch through the generated app/shortcut, and exercise uninstall.
- **Explicit uninstall:** release assets include guarded uninstall helpers that require confirmation before deleting the private runtime, configuration and local evidence, and refuse while port 8787 is in use.
- **Compatibility:** existing manual ZIP install paths remain available. The installer work does not replace the established capture, local evidence, privacy, Gateway, browser-evidence or agent-integration architecture.

---

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

<!-- Source: docs/CHANGELOG_V0109.md -->
# OpenWorkGraph v0.109 — grounded agent session continuity

v0.109 adds a native agent-session sensor and cross-agent handoff without changing the canonical-evidence model or silently expanding capture.

## Native session observation

- Claude Code and Codex local session files can be tailed directly, without modifying the agent's configuration.
- The native sensor is **off by default** and must be enabled locally.
- On first enable, and on every re-enable after observation/source capture was turned off, existing files are primed to their current end. OpenWorkGraph does not silently backfill old sessions or replay work from a disabled interval.
- New native records are projected into the existing provider-neutral structural agent schema. Raw provider records are never persisted.
- If a hook, SDK or OpenTelemetry adapter already reported the same structural step, that richer evidence wins and the native-file fallback is suppressed.
- Claude Code gets a start boundary only for a newly observed session file; OpenWorkGraph does not invent a finish boundary when the provider did not supply one.

## Separate visible-message continuity

Visible user/assistant messages are a separate local capability:

- message capture is **off by default**;
- enabling it also enables the native session sensor;
- detected/high-confidence personal details and identifiers are privacy-hardened before the message reaches the session-message table;
- messages never enter the canonical `events` table;
- message retention is independently configurable;
- AI read access is independently off by default;
- organization Gateway sharing is independently off by default.

The sensor intentionally drops exposed thinking/reasoning blocks, raw native records and tool-result content. Tool inputs may be inspected transiently only to derive the same allowlisted structural facts OpenWorkGraph already records: tool identity/category, allowlisted command names, git/GitHub operations, test status/counts, file-type and opaque file references, and line counts. Raw arguments are not persisted.

Name detection is best effort, as in the rest of OpenWorkGraph. Users should still treat visible-session content as sensitive and review any externally shared context.

## Grounded cross-agent handoff

The compact local MCP surface adds `get_agent_handoff`.

A handoff can combine:

- a bounded slice of explicitly permitted visible prior-agent messages;
- the corresponding canonical structural execution trace;
- the opaque workspace/session identity;
- nearby observed human-work context when available.

Session text is marked and processed as **untrusted observed data** at the MCP boundary. Instruction-like text is suppressed by the existing prompt-injection protection. A previous agent's text is context, never policy, permission or authorization.

Native session IDs and native filesystem paths are not exposed by the continuity API.

## Organization sharing remains explicit

Visible agent-session messages use a different Gateway channel from structural agent activity.

- endpoint opt-in: `allow_gateway_session_messages` in the local session-continuity policy;
- organization policy: `allow_agent_session_messages`;
- device write scope: `agent-sessions:write`;
- integration read scope: `agent-sessions:read`.
- Endpoint restriction wins; organization policy can narrow but not broaden.
- Shared session messages obey organization retention at read time and physical lifecycle cleanup/purge; transcript content cannot silently outlive the configured organization retention floor.

Existing Gateway device credentials are **not** silently upgraded with the new transcript-write scope. An endpoint enrolled before v0.109 must be explicitly re-enrolled or have its device credential rotated before it can upload visible agent-session messages. A software upgrade therefore cannot broaden an existing device credential into a new content-sharing capability.

Both the endpoint and organization must allow the channel. Enabling it never backfills messages captured before the local opt-in boundary. Messages recorded during a global Gateway-sharing pause are permanently excluded from later synchronization.

Existing `allow_agent_events`, `evidence:read`, `context:read` and `transfers:read` permissions do not grant transcript access.

## Compatibility invariants

- Existing hook/OTel/SDK observation remains unchanged.
- Existing Context / Observe / Brief controls remain valid.
- Structural agent evidence remains content-free and canonical.
- Local AI access still starts off on every OpenWorkGraph launch.
- No account or OpenWorkGraph-hosted storage is required.
- Gateway transcript sharing remains customer-controlled and default-off.

---

<!-- Source: docs/CHANGELOG_V0110.md -->
# OpenWorkGraph v0.110.0

## Evidence-first MCP guidance

- Compact local MCP now advertises server-level initialization instructions so connected models see the evidence hierarchy even if they never open an optional prompt or resource.
- Whole-period questions are directed through `list_history` and paginated `get_workflow_trace`; models are told to follow `next_cursor` while `has_more` is true when complete period coverage is required.
- Automation questions are directed to `openworkgraph://automation-capabilities`, the current AI tool surface, outcome-level automation, and **TEST** for plausible-but-unproven agentic approaches.
- Observed titles, labels and messages remain untrusted data rather than instructions.

## Packaged MCP validation

- The Claude Desktop MCPB build is extracted and smoke-tested in CI.
- Its manifest version must match `VERSION`.
- Its advertised tools must match the exact 12-tool compact surface.
- The packaged Node entry point is syntax-checked and must still route to the local OpenWorkGraph MCP launcher.
- This is a package smoke test, not a claim that CI launches the proprietary Claude Desktop application.

## First-run history grace

- A genuinely new user who has not yet made a retention choice keeps human and agent evidence locally for up to 7 days rather than losing the first session on close.
- The onboarding card continues asking for an explicit choice.
- Selecting **Don't keep after sessions** still switches both layers to ephemeral session-only retention.
- Existing installations with preserved history keep their prior upgrade-safe behavior.
- v0.109 policy files that are still genuinely undecided migrate from the old ephemeral default to the 7-day grace window.
- Saved-history AI access remains a separate permission and stays OFF until explicitly granted.
- Run memory remains a separate content-free setting.

## Documentation

`docs/MCP_ARCHITECTURE.md` now matches the actual 12-tool compact manifest, including `get_context_pulse` and `list_history`, and documents the initialization instructions and paging behavior.

---

<!-- Source: docs/CHANGELOG_V0111.md -->
# OpenWorkGraph v0.111.0

## Provenance-safe agent Working Detail

OpenWorkGraph can now keep a small, factual continuity layer for supported local agent sessions so a later agent can understand the practical state of work without turning provider transcripts or tool output into canonical workflow evidence.

Working Detail is separate from the canonical `events` stream. It is OFF by default, has its own retention policy, and connected-AI read access is a separate OFF-by-default permission. Disabling it leaves the existing structural observer and session-continuity behavior unchanged.

### What can be retained

When explicitly enabled, Working Detail can retain bounded, privacy-hardened facts such as workspace-relative files, allowlisted command/program names, test outcomes and counts, failing test identifiers, short redacted error excerpts, and repository state such as dirty state, short commit identity, branch name, and bounded changed-file paths. Each record carries provenance and is marked non-authoritative observed context.

Test outcomes are tri-state: passing, failing, or unknown. Missing result evidence stays unknown. A zero observed-failure count is not treated as proof that a test run passed.

### What is not retained

Raw tool output is not stored. Arbitrary shell arguments, absolute workspace paths, prompts, visible conversation, provider-native records, secrets, and hidden reasoning are not Working Detail. Visible user/assistant messages remain governed by the existing separate session-message controls.

File paths must resolve inside the observed workspace and are stored workspace-relative. Traversal and outside-workspace paths are rejected. Working Detail does not broaden organization Gateway sharing in v0.111.

### Capture and import behavior

Normal enablement starts at the current end of supported local Claude Code and Codex session history, so an upgrade or first enable does not silently backfill prior sessions. An explicit historical import can be requested for a bounded recent window. Historical tool-result text is parsed in memory to derive structured facts and then discarded; raw tool output and hidden reasoning are not imported.

Working Detail scanning has an independent cursor and runtime. A Working Detail parse or storage failure is fail-open with respect to the canonical structural observer and must not block normal OpenWorkGraph capture.

### Handoff and compatibility

The existing agent handoff can include bounded Working Detail when the relevant local capture and connected-AI read permissions allow it. The compact MCP tool surface does not gain a new tool. Existing grounding keys and legacy agent-run summary shapes remain compatible.

The canonical event schema is unchanged in this release.

---

<!-- Source: docs/CHANGELOG_V0112.md -->
# OpenWorkGraph v0.112.0

## Trustworthy AI answers and agent-run accounting

v0.112.0 hardens the accuracy of the evidence OpenWorkGraph returns to connected AI while keeping the existing local-first, evidence-first privacy model intact.

### One physical Claude Code session stays one execution

Claude Code hook/OTel observations and native-session fallback now use the same **opaque** cross-sensor session identity. Top-level prompt turns remain observations inside the physical session instead of becoming separate executions, while genuine subagents retain separate opaque child execution identities.

Tool calls observed by both hooks and transcript fallback share an opaque tool identity as well. This lets OpenWorkGraph prefer the richer hook/OTel observation even when the transcript timestamp differs, without collapsing legitimate adjacent calls merely because they used the same tool.

Raw native session, prompt-turn, tool-use, trace and span identifiers are not exposed. Existing retained rows created before v0.112 are not silently rewritten; newly observed evidence uses the corrected identity scheme.

### Test outcomes stay passing, failing or unknown

Agent-run summaries distinguish known passing test runs, known failing test runs and runs whose result is **unknown**. Missing result evidence is never represented as zero failures.

When an otherwise-unknown run has observed test evidence, the latest observed test result can resolve the run outcome: a final passing run may resolve to success and a final failing run may resolve to error, while earlier observed failures remain counted in the run summary.

### Token usage is observed evidence, never an estimate

**Token usage** is reported only when provider, SDK or compatible telemetry exposes exact usage counters. Runs with such evidence report token usage as `observed` with the recorded counts.

When the observation surface does not expose token usage, OpenWorkGraph reports `not_observed` with a basis and empty counts. It does not estimate tokens from visible text, elapsed time, screen activity or model identity, and it never treats an unobserved token signal as zero.

### Smaller default MCP evidence pages

`get_workflow_trace` now returns compact rows by default with a smaller default page size while preserving stable pagination and canonical chronology. Connected AI can explicitly request `detail="rich"` for the full authorized row.

For questions covering a whole period, models should continue following `next_cursor` until `has_more` is false. Compact mode changes payload size, not the underlying evidence or retention/access boundary.

### Better default scope after restart

`get_work_profile(scope="current")` and the compact current-work overview can fall back to authorized evidence from **local today** when the current launcher session is empty after a restart. Responses disclose the requested and actually used scope and include a hint explaining the fallback.

Saved-history access remains a separate permission. If the connected AI lacks the required access, OpenWorkGraph explains that instead of silently broadening the scope.

### More readable repeated workflows

Repeated-workflow candidates now expose readable typical steps and use those steps for the display label rather than relying only on a guessed task label.

Engaged-time and foreground-time evidence remain distinct. When engaged time is zero or unobserved but foreground duration exists, the response may expose an explicit foreground-time fallback while preserving the original engaged and foreground metrics and identifying the duration basis.

### Clearer saved-history remediation

Aggregate MCP tools that require all saved history now name the exact remedy: grant **All saved history** in History, or use `get_workflow_trace` with `since`/`until` inside the already granted date range when that satisfies the question.

### Native session sensor health

Clean bootstrap and cleanup paths no longer increment the native-session sensor error counter. Real failures increment the counter and expose only a bounded `last_error` stage plus exception type; local paths, provider payloads and content are not included.

### Privacy and compatibility boundaries remain intact

- The compact MCP surface remains 12 tools.
- Canonical workflow evidence remains the source of truth; derived summaries remain non-authoritative.
- Hidden reasoning is not captured or exposed.
- Ordinary **raw tool output** is not persisted by this accuracy work.
- Arbitrary shell arguments, typed text and clipboard contents are not added to canonical evidence.
- Existing Redacted/Full disclosure controls and saved-history authorization remain in force.
- Token values are never inferred when the runtime does not expose them.

---

<!-- Source: docs/CHANGELOG_V0113.md -->
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

---

<!-- Source: docs/CHANGELOG_V0114.md -->
# OpenWorkGraph v0.114.0

## Configurable browser context without weakening the privacy-first path

v0.114 adds an explicit privacy model for linking observed browser work to the business objects it operates on. The feature is additive: business-object reference capture remains off by default, existing browser/desktop evidence continues to work, and no content connector or duplicate company-data store is introduced.

### Three browser-context privacy profiles

- **Privacy-first** keeps business-object references off. It also masks known object-ID positions in stored Google Docs/Drive, GitHub, Salesforce, Jira and Linear paths before persistence. This is intentionally stricter than the pre-v0.114 URL-path behavior.
- **Context** recognizes allowlisted objects and keeps only an installation-keyed local correlation token. Provider record/thread/document identifiers are not retained.
- **Rich enterprise** is an explicit opt-in that may retain the minimal validated provider-specific locator needed for an authorized connector or AI to resolve the object. It does not enable unrelated optional sensors.

The same underlying switches remain individually configurable, so organizations and local users can choose a custom combination instead of a preset.

### Allowlisted business-object recognition

The first reference parsers cover:

- Google Docs, Sheets, Slides and Drive files;
- Gmail web conversation locators on known conversation routes;
- GitHub pull requests and issues;
- Salesforce records;
- Jira issues; and
- Linear issues.

Unknown sites and sensitive-looking arbitrary routes are not guessed. They fall back to the existing sanitized browser evidence.

### Two keyed persistence boundaries

Context correlation tokens are no longer plain hashes of provider IDs.

1. Before a resource-reference event can enter the browser extension's durable retry queue, the extension HMACs the canonical allowlisted reference with the installation browser-pairing secret. The queue therefore contains an opaque `owg:e:…` sensor fingerprint rather than a dictionary-attackable hash of a short Jira key or PR number.
2. Before local event persistence, the server replaces that sensor fingerprint with a different `owg:r:…` HMAC keyed by the installation API secret. The raw browser fingerprint is not stored in the canonical event row.

Rich enterprise mode additionally validates that a supplied locator and browser fingerprint agree. Re-hardening an already persisted local token is idempotent.

These local capability secrets remain subject to the existing same-operating-system-user threat-model limitation described in `PRIVACY_AND_DATA.md`.

### Existing URL privacy remains in force

This release does **not** add storage of:

- full browser URLs;
- URL query values or fragments;
- typed text;
- clipboard contents;
- page contents;
- password-field values; or
- arbitrary filenames/file contents.

Known resource references are extracted only through the allowlist before generic URL sanitization removes their identifier. Host/title exclusion policy is checked before a Rich enterprise locator can enter the extension retry queue, and the server independently applies the policy again at ingest.

### Browser sensor update notice

The browser extension is v1.14.0 for this release. The local server already reports the expected and observed sensor versions; the dashboard now turns a mismatch into an explicit **Browser sensor update available** notice telling unpacked-extension users to reload it. Existing capture continues while the old sensor is running, but v0.114 browser-context features remain unavailable until the sensor is current.

### Compatibility

- No canonical event schema is replaced.
- Existing desktop capture, browser semantic capture, agent observation, MCP tools and Gateway data model remain intact.
- The resource-reference path is opt-in and uses ordinary privacy-hardened browser events.
- Native macOS/Windows active-URL capture is not part of v0.114; the browser extension remains the richer browser-semantic source.
- No Google Workspace, Microsoft 365, Salesforce or other content-ingestion connector is added.

---

<!-- Source: docs/CHANGELOG_V0115.md -->
# OpenWorkGraph v0.115.0

## Human-first product demo

OpenWorkGraph's core product still observes ordinary human desktop/browser workflows without requiring an AI agent. v0.115 makes the demo and downloadable tester packages show that clearly.

- The primary synthetic demo is now three repeated **human-only** renewal workflows: Gmail → Salesforce → Google Sheets → Salesforce → Gmail.
- The human workflow uses the same evidence shapes as live capture: focus/timing, browser semantic events, aggregate input activity and content-free copy/paste linkage.
- The demo runs its isolated browser evidence in **Context** mode, so repeated fake Gmail/Salesforce/Sheets objects receive installation-keyed `owg:r:…` correlation tokens without retaining provider locators.
- Browser paths use masked object placeholders rather than fake provider IDs. Typed text, clipboard contents, page contents and full URLs are not added to the demo.
- A separate human + coding-agent example appears later in the timeline. The agent rows use OpenWorkGraph's real structural agent-ingest contract and are deliberately secondary to the human-only workflow.
- The demo output explicitly states `agent_required_for_human_capture: false`.

## One runtime for live mode and demo mode

- `START_ON_MAC.command` and `START_ON_WINDOWS.ps1` now accept an explicit `observe` or `demo` mode while retaining live observation as the default.
- The old Windows demo launcher no longer requires a separately installed Python or uses the old Workflow Observer setup wording; it delegates to OpenWorkGraph's private-runtime installer.
- The macOS demo launcher also delegates to the same private-runtime installer as live mode instead of maintaining a duplicate setup path.

## Demo included in the downloadable ZIPs

The macOS and Windows release ZIPs now expose two clear entry points:

- `START_OPENWORKGRAPH...` — real local human workflow observation.
- `TRY_DEMO_OPENWORKGRAPH...` — isolated synthetic sample evidence.

`README_FIRST.txt` in both packages explicitly says that AI agents are optional and describes the human-first demo before the separate agent example.

The GitHub README download buttons continue to use `releases/latest`, so once v0.115 is published they resolve to these refreshed ZIPs automatically.

## Browser sensor version

The browser extension remains **1.14.0**. No extension code or browser protocol changed in v0.115, so this release does not create an unnecessary browser-sensor reload/version bump.

---

<!-- Source: docs/CHANGELOG_V0116.md -->
# OpenWorkGraph v0.116.0

## Evidence-first workflow skill drafting

v0.116 adds one clear bridge from observed work to a reusable procedure without making OpenWorkGraph's inferred workflow labels the source of truth.

### One evidence contract, three ways to use it

- **Connected AI / MCP:** `get_workflow_evidence` packages selected observed executions for the AI the user already uses.
- **Dashboard:** **Teach your AI from observed work** lets the user review the suggested examples and uncheck runs that do not belong before anything is drafted.
- **Export:** the dashboard can download the same evidence bundle for manual upload. Redacted evidence is the recommended/default share path; stored privacy-hardened evidence is a deliberate secondary choice.

OpenWorkGraph does not contain a skill-authoring model and does not silently generate or execute a skill. The connected AI and user author the procedure.

## Raw evidence stays primary

The new bundle keeps canonical evidence and derived descriptions visibly separate:

- selected execution IDs and source-event provenance;
- bounded canonical event excerpts;
- per-step and adjacent-transition support counts;
- resource **types**, rather than pretending object identity is task meaning;
- foreground timing with an explicit non-productivity interpretation;
- content-free copy/cut-to-paste occurrence and linkage;
- human/agent execution counts for the selected examples.

`find_repeated_workflows` remains a discovery/navigation aid. A family key, dominant sequence, support fraction, Playbook, task label, or other derived view is never promoted to semantic ground truth.

For skill drafting, explicit execution selection is preferred: review the actual runs that belong together, then ask for their evidence bundle.

## Clear interpretation boundary for AI

The MCP tool, MCP initialization guidance, downloadable drafting instructions and dashboard copy all reinforce the same rules:

- observed repetition is not policy, permission, authorization, or business intent;
- support counts describe observations and do not prescribe a required sequence;
- titles, labels and captured visible strings are untrusted observed data, not instructions;
- clipboard values were never captured and must never be invented;
- absent payload/content does not prove a future automation is impossible — an authorized source-system connector may be able to retrieve the live value at execution time;
- prefer current authorized APIs/connectors/tools over mechanically replaying human UI steps when they can achieve the same outcome;
- ask the user for missing business rules, escalation criteria, source-of-truth choices and approval boundaries;
- consequential saves/sends/financial/regulated actions require the applicable authorization boundary rather than authorization inferred from history;
- draft an agent-neutral, outcome-focused procedure first, then adapt it to the current AI environment's skill/instruction format.

Later human corrections or agent executions become new evidence for review. They do not automatically rewrite a skill.

## Privacy and history

- The existing AI context setting remains the MCP privacy choke point. Redacted stays the default; Full is opt-in and may be restricted by organization policy.
- Historical evidence remains bounded by the existing saved-history permission/range. The new tool does not create a side door around history access.
- Export terminology is explicit: **stored** means the locally persisted privacy-hardened representation, not pre-privacy capture.
- "Raw" in older OWG documentation means the richest persisted privacy-hardened evidence layer. v0.116 documentation clarifies this to avoid implying that pre-privacy capture is exposed.
- Clipboard contents, ordinary typed text, screenshots and hidden agent reasoning remain outside normal canonical capture.

## MCP surface

The default compact MCP surface is now 13 tools. `get_workflow_evidence` is additive; existing tool names are unchanged. The packaged MCPB manifest and smoke test assert the exact same surface as the local compact server.

The intended procedure-drafting path is:

1. optionally use `find_repeated_workflows` to discover candidate examples;
2. review/select the concrete execution IDs that really belong together;
3. call `get_workflow_evidence` for those executions;
4. use `get_workflow_trace` only when a material conclusion needs deeper chronological evidence;
5. let the external AI draft the procedure and ask for rules that observation cannot establish.

`get_playbooks` remains descriptive prior-run/playbook memory and is not authority for what a new procedure must do.

## Dashboard security and UX

Workflow-evidence ZIP downloads use the same authenticated in-memory dashboard session as other protected local API requests. The dashboard fetches the archive through the authenticated `/v1` boundary and then downloads the returned blob; v0.116 does not weaken the API by making the export route public.

The UI deliberately avoids a second workflow product or an embedded model selector. Users see one action in the existing Repeated Workflows area: **Teach your AI from observed work**.

## Release and validation

- Version sources are aligned at `0.116.0`.
- MCPB packaging asserts the 13-tool compact surface including `get_workflow_evidence`.
- Contract tests use hand-constructed executions rather than trusting current family-clustering heuristics.
- Tests assert explicit execution selection, support/provenance semantics, non-authority of derived families, clipboard-content boundaries, connected-AI drafting rules, and authenticated redacted-first dashboard export behavior.

---

<!-- Source: docs/CHANGELOG_V0117.md -->
# OpenWorkGraph v0.117.0

## A clearer, calmer dashboard

v0.117 is a dashboard release. Capture, storage, MCP and the browser sensor are unchanged (browser sensor 1.14.0 stays current); what changes is what you see and where you find it.

### Fixed

- **Today's timeline shows again.** A first-run helper hid the timeline card before the timeline had loaded, and nothing ever showed it again. The card now stays visible with its own loading and empty states, and it refreshes when you switch back to Overview.
- **Evidence has a way back.** After choosing Navigation loops or Transitions there was no button to return to the event list. An **Events** button now sits next to them.
- **No stale version number.** The top bar briefly showed v0.56.1 or v0.57.0 before the real version loaded. It now shows nothing until the real version arrives.
- **One recording clock.** Two scripts used to overwrite the recording label in turn. The capture status now owns it, and the elapsed time ticks every second locally.
- Self-tag and capture-settings confirmations used a toast function that did not exist; they now show.

### Evidence keeps its context

The Evidence table used to show only the tool name in the Page column. It now shows the stored page or window title, the same context exports and redacted AI context already keep (for example "Re: Contract renewal Q4 - PERSON_1A2B3C - Gmail"). Detected names, email addresses, phone numbers and personal identity numbers are tokenized before storage, and the dashboard runs the same best-effort protection once more for display, including legacy rows. Display protection runs before title truncation so a cutoff cannot turn a sensitive identifier into an unrecognisable fragment. It fails closed: if the check cannot run, only the tool name is shown. URL paths and raw button labels are still never sent to the dashboard.

### Human views show human work

- **Evidence** lists your own work only. Agent runs (Claude Code, Codex, Cursor and others) are on the Agents tab.
- **Work profile** is computed from your own work only, so a coding agent no longer appears as "AI-tool usage" with zero minutes, and agent activity no longer feeds the transfer, repeated-page or friction signals.
- **Playbooks** follow the Agents tab's choice: workflows run only by agents whose Observe switch is off are hidden unless you tick "Show past runs from agents whose Observe is off".

### One place for AI access

There used to be three: a switch in Connections, a second Enable button in an "AI access" card, and saved-history access in History. Now:

- **Connect** has the one switch, "AI access this run", with a plain explanation that it turns itself off every time OpenWorkGraph starts, and a summary line of everything AI apps can read (this run, and older saved history with a link to change it).
- The old second card is now **Recent AI activity**, a read-only log of what was read.
- **History** keeps the saved-history permission and points back to the summary in Connect.

### Settings tab

Personal capture settings moved out of **Organization** into a new **Settings** tab: Capture & privacy (privacy profile, browser signals), Browser sensor pairing, and learned names. Organization now only holds joining and sharing with an organization. The "Browser sensor not connected" chip opens Settings.

### Overview reads in order

Overview now shows what happened first (metrics, Today's timeline, Repeated workflows, Time by tool) and the Work profile after it, since the profile interprets those. Repeated workflows and Time by tool show an explanation when empty instead of disappearing.

### Plain language

- "Work surfaces" is now "Tools used"; "Effort by work surface" is "Time by tool".
- "Navigation / hunting candidates" is "Pages you kept going back to"; "Friction candidates" is "Possible friction" with "Repeated clicking" and "Sign-in steps"; "Tool waiting" is "Waiting for pages to load".
- Agent observation levels show as "Full trace", "Tool calls only", "App activity only" and so on, not internal codes.
- Run memory and Playbooks no longer show internal workflow keys.
- Notes that referred to unbuilt "stacked PRs" are gone.

### Controls

- Run memory, outcome tracking and brief measurement are switches that save immediately, with a confirmation when turning one off deletes data. No separate Save button.
- Delete buttons are quiet until you mean it: Evidence deletion moved below the table, and the final "Delete evidence" confirmation is the red action. Deleting an imported playbook now asks first.
- Selects, date pickers, file pickers, text areas, checkboxes and switches share one style across tabs.

### Performance and small screens

- The dashboard checks capture, AI-access, organization and timeline state every 5 seconds (it was every second), and only while the page is visible.
- On a phone, the tab bar fades at the edge to show it scrolls, the selected tab scrolls into view, and Work profile tiles use two columns.

---

<!-- Source: docs/CHANGELOG_V0118.md -->
# OpenWorkGraph v0.118.0

## Basic and Advanced views, one Privacy tab

The dashboard had grown to eight tabs, about 110 controls and 2,600 words on a new install, with privacy choices spread over seven tabs. v0.118 opens in a **Basic** view with four tabs and puts every everyday privacy choice in one place. Nothing was removed: the **Advanced** switch in the top bar (remembered on this computer) shows every tab and setting as before.

### Basic view

- **Today:** one status line ("11 min of work recorded so far · 5 tools · AI apps can't read it · nothing shared"), **See what happened**, and a three-step setup checklist (how long to keep history, browser sensor, connect an AI app) that disappears when done. Then the timeline, time by tool and repeated workflows. Work profile, Workflow discovery, idle time and key presses are in Advanced.
- **Activity:** the list of what was recorded, search and delete, with **Export…** at the top.
- **AI apps:** the apps, their switches and the read log. Scripting help and redaction lists are in Advanced.
- **Privacy:** see below.
- **Agents** and **Organization** appear in Basic as soon as they are in use (a coding agent is observed, or this computer joins an organization).

### Privacy tab

One page for everything a person usually wants to control:

- **Recording:** pause or resume.
- **Keep my history:** this session only, 7, 30 or 90 days, 1 year, or until you delete it. One choice now covers everything kept, including the small run summaries, so "This session only" really keeps nothing after the session.
- **AI apps can read my work**, **Include older history** (24 hours, always switches itself off) and **Hide names and contact details from AI apps**.
- **Browser detail:** Standard or More context.
- **Never record:** apps, websites and words in a window title. These lists existed before only in a config file; now they can be seen and edited, they apply to new activity immediately (the server applies them on arrival, so the recorder does not need a restart), and the default list (password managers; titles with password, private, incognito or bank) can be restored.
- **Delete recorded activity:** last 15 minutes, last hour, today or everything, each with a confirmation.

### AI access is remembered

The AI access switch used to turn itself off at every restart while each app's Context switch stayed on, so connected apps stopped working after a restart without an obvious reason. It is still **off on a new install**, every MCP call is still checked live, and the read log is unchanged; now your choice is remembered after a restart. **Turn AI access off every time OpenWorkGraph starts** (Privacy, Advanced view) restores the previous behavior. The state is stored with owner-only permissions; if the file cannot be read, access stays off.

### Fixes

- A dashboard tab kept open across a restart now says so and explains how to reopen it, instead of showing "unavailable" everywhere.
- **Export** includes summaries by default; every individual event is an explicit choice.
- **What would be shared?** no longer shows a "Window titles: Yes" table when no organization is connected; it says nothing is shared.
- The first-run "Getting started" and "How long should OpenWorkGraph keep this?" cards are replaced by the Today status line and checklist in both views; the reconstruction is still one click away.

### Look and feel

A calmer visual layer: segmented tabs, softer cards and shadows, one type scale, a recording indicator, switches and chips that match, and layouts that hold up at phone width.

---

