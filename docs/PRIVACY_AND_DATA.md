# Privacy and data handling

OpenWorkGraph is designed to collect enough workflow evidence for useful process/context analysis without becoming a screen recorder or keylogger.

This document describes the current v0.114 behavior. It is a technical description of the prototype, not a claim that every future enterprise deployment has the same policy requirements.

## Local-first boundary

The current local server binds to loopback (`127.0.0.1`). Captured event data is stored on the local machine unless the user deliberately exports it or connects OpenWorkGraph to another system.

Loopback binding alone is not treated as authentication. v0.48 adds installation-local capability controls around the local interfaces:

- raw/history/export/control API reads require either an authenticated dashboard session or the installation API capability token
- desktop collector writes are authenticated with the installation API capability
- Streamable-HTTP MCP requires a separate local MCP bearer capability
- the browser sensor proves the local OpenWorkGraph server knows its pairing secret before browser evidence is sent
- browser sensor requests are HMAC-signed with timestamp/nonces so unauthenticated local processes cannot inject arbitrary browser events without the pairing secret
- repeated browser request signatures are rejected to reduce replay

The long-lived API/MCP/pairing secrets are generated locally and stored under the installation auth directory. On POSIX systems OpenWorkGraph attempts to enforce `0700` on the auth directory and `0600` on secret files. On Windows the files inherit the current user's application-data ACLs.

### Dashboard authentication

The dashboard does **not** receive the long-lived API token in its HTML.

The launcher opens the dashboard with a random bootstrap secret in the URL fragment (`#...`). URL fragments are not sent with the initial HTTP request. The dashboard exchanges that launcher-only value for an HttpOnly, SameSite local session cookie and removes the fragment from the visible URL state.

An unauthenticated process can still fetch the non-sensitive dashboard shell, but data-bearing `/v1/*` routes fail closed until the caller presents a valid capability/session.

### Browser server authentication

The browser extension no longer sends rich page/UI evidence merely because something is listening on `127.0.0.1:8787`.

Before an authenticated browser API request, the extension sends a random challenge. Genuine OpenWorkGraph returns an HMAC proof derived from the installation browser-pairing secret. The extension verifies that proof locally before sending the actual event. The event request itself is then HMAC-signed and replay-protected.

Normal standalone installs write a local `browser_extension/pairing.json` credential at runtime before the unpacked extension is used. That file is generated locally and is not part of the repository/release. If automatic pairing is lost, the dashboard can issue a short-lived one-time 8-digit recovery code and the extension popup can pair again. Repeated wrong attempts invalidate the code.

If authentication fails, the extension's existing durable queue retains pending events rather than sending them to an unverified localhost service.

## Important threat-model limit

These controls raise the bar substantially over an unauthenticated localhost port and protect against accidental localhost probing, unrelated local services, naive port squatters, misconfigured tools/proxies, and processes that do not possess the installation credentials.

They are **not** intended to protect against malware already running with the same operating-system user privileges. A same-user malicious process may be able to read local credential files, inspect process state, or access other resources owned by that user. Stronger protection against that class of attacker requires operating-system isolation, privilege separation, endpoint security, and/or enterprise deployment controls.

"Local" therefore means local storage/network scope by default; it should not be interpreted as an absolute confidentiality boundary against code already executing as the same user.

## What OpenWorkGraph captures

Current signals include:

- active application/window focus spans
- browser tab activation and navigation when the optional browser sensor is installed
- foreground, engaged, probable-idle and active-input timing
- away spans: time with no keyboard or mouse input for 5 minutes (configurable), or with the screen locked. They are read from the operating system's idle clock, which reports only when the last input happened and never which key was pressed. Away spans stay on this computer and are never sent to an organization Gateway, whatever its policy.
- keypress counts
- global clicks and throttled scrolls
- safe native control identity/label metadata through macOS Accessibility or Windows UI Automation, best effort
- browser semantic events such as interactive clicks, editor/input focus, form submit and control change
- occurrence of copy/paste: copy, cut and paste shortcuts, plus clipboard writes without a shortcut (menu, right-click, drag, apps). Writes are noticed from the OS clipboard change counter, which never exposes contents. A write is never recorded as a paste, and nothing is attributed while you are away.
- derived task/process/effort structures
- for observed coding agents: which allowlisted programs and git/GitHub operations a tool call ran, test pass/fail counts, file types, keyed hashes of file paths, and lines changed. These are derived in memory from the tool's input and output; the command text, paths, file contents and output are never stored (see `docs/NATIVE_AGENT_ADAPTERS.md`, "Tool detail").

## What is deliberately not captured

Normal operation does not store:

- typed text
- individual key identities or key order
- clipboard contents
- password-field values
- selected text
- screenshots or screen recordings by default
- browser URL query strings or fragments

The keyboard sensor reports counts/timing only.

Native accessibility/UI Automation code deliberately avoids value/text patterns that would expose field contents.

## Browser-context privacy profiles (v0.114)

Business-object references are a separate optional browser-context layer. They do not turn typed text, clipboard contents, page contents or arbitrary full URLs into capture inputs.

| Profile | Business-object reference | Provider locator | Stored browser path |
|---|---|---|---|
| **Privacy-first** (default) | Off | No | Known Google Docs/Drive, GitHub, Salesforce, Jira and Linear object-ID positions are masked to `:id`. This is deliberately stricter than pre-v0.114 path behavior. |
| **Context** | On | No | Same known object-ID positions are masked; repeated observations can share an installation-keyed `owg:r:…` token. |
| **Rich enterprise** | On | Yes, only after allowlist validation | The ordinary generic URL sanitizer still removes query/fragment/secret-like material; the separately validated minimal locator may be retained for authorized connector resolution. |
| **Custom** | User-selected | User-selected, but only when references are on | Follows the selected switches. |

The initial allowlist recognizes Google Docs/Sheets/Slides/Drive objects, known Gmail conversation routes, GitHub pull requests/issues, Salesforce records, Jira issues and Linear issues. Unknown sites and arbitrary sensitive-looking routes are not guessed; they fall back to ordinary sanitized browser evidence.

### Resource-reference token boundaries

Context mode is intended to support correlation without turning short provider IDs into a reversible local identifier database. v0.114 therefore uses two keyed stages rather than a plain SHA-256 of the provider locator:

1. **Browser durable queue:** before a resource-reference event can enter the extension retry queue, the paired extension HMACs the canonical allowlisted reference with the installation browser-pairing secret. The queue-side `owg:e:…` fingerprint is therefore not recoverable by simply enumerating likely Jira keys, GitHub PR numbers or Linear issue keys and hashing them.
2. **Local event persistence:** at ingest the server independently validates the provider/kind/host and, when present, the locator. It then replaces the browser fingerprint with a different `owg:r:…` HMAC keyed by the installation API secret. The incoming browser fingerprint is not retained in the persisted event.
3. **Rich enterprise validation:** when a locator is present, the server derives the expected browser fingerprint from the validated locator and rejects a conflicting supplied fingerprint before storing anything.

The resulting tokens are stable within one installation while the relevant local capability secrets remain unchanged. Different installations produce different persisted tokens for the same source object. Re-hardening an already persisted token is idempotent.

These keys are local capability secrets under the same threat-model limit described above: this protects against offline dictionary recovery from a copied evidence token, not against malware already able to read the current user's OWG credential files.

### Capture versus disclosure

The profile controls what this browser-context feature may retain locally. It does **not** by itself grant an AI or an organization access to that data. Connected-AI detail, export choices and Gateway sharing remain separate boundaries. An organization may narrow endpoint sharing; it does not make a locally stricter capture choice richer.

Host/title exclusions are applied before an optional Rich enterprise locator can enter the browser retry queue, and the local server applies the same exclusion policy again at ingest. Incognito/private extension operation remains disabled.

The browser sensor version is also checked independently. When the local server observes an older unpacked extension, the dashboard displays **Browser sensor update available** and tells the user to reload it. Ordinary existing capture can continue, but v0.114 resource-reference behavior should not be treated as active until the expected sensor version is running.

### Optional agent-session continuity (v0.109)

Visible agent-session messages are a deliberate exception to the normal no-typed-text capture rule, and only when explicitly enabled by the local user. The feature is **OFF by default**. Turning native observation or a source OFF creates a hard capture boundary: re-enabling starts at the then-current end of the native session files rather than replaying activity from the disabled interval.

- Supported native Claude Code/Codex session files can always be used for privacy-minimized **structural** observation only after the separate native-session sensor is enabled.
- Saving visible user/assistant messages is a second explicit switch. Those messages live in `agent_session_messages`, never in canonical `events`.
- Detected/high-confidence personal details are privacy-hardened before message persistence; name detection remains best effort.
- Hidden reasoning/thinking, raw provider records and tool-result content are not stored as continuity messages. Tool inputs are inspected only transiently to derive the existing allowlisted structural facts.
- Local message retention is independent (30 days by default), connected-AI reads are independently OFF by default, and Gateway sharing is independently OFF by default.
- A connected AI receives saved visible messages only through the grounded handoff boundary after the user enables AI message access. The messages are treated as untrusted observed data, not instructions, policy or authorization.
- Organization sharing requires endpoint opt-in, organization opt-in and dedicated credential scopes. Existing pre-v0.109 device credentials are not silently broadened. Shared transcript rows obey the organization's retention floor at read time and physical cleanup/purge.

## ChatGPT public plugin data boundary

The public ChatGPT plugin is an optional remote read path. Installing or using the
plugin does not make a local-only OpenWorkGraph installation upload history by
itself. The public plugin can read only privacy-hardened evidence that the endpoint
has separately opted to synchronize to an OpenWorkGraph Gateway.

- Local capture remains independent of the plugin and Gateway.
- OAuth identifies the plugin account; every MCP read is scoped from the validated
  token rather than a model-supplied user, actor, device or organization ID. The
  public profile tool returns a keyed opaque profile ID and may return the OAuth
  provider's `name` and `email` claims when present so users can distinguish connected
  accounts. Raw subject, organization, actor and device identifiers are not returned
  by public evidence tools.
- The plugin is read-only. It cannot create, edit, send or delete records in source
  business systems.
- Repeated-work candidates and workflow alignments are deterministic derived indexes
  over the synchronized evidence. They are navigation aids, not policy, permission,
  productivity scores or semantic ground truth.
- Canonical local evidence may be richer than the synchronized Gateway subset. A
  missing remote result therefore does not prove that the work never occurred.
- Historical behavior never authorizes a current action. When ChatGPT has an
  authorized live connector for the underlying system, OWG evidence should identify
  relevant work/resources and the live connector should supply current state.
- Disconnecting/revoking the plugin stops future plugin reads. Gateway retention and
  deletion remain governed by the configured Gateway lifecycle; disconnecting the
  plugin does not silently delete local history.
- OAuth access tokens and refresh tokens are authentication material and must not be
  stored in canonical OWG evidence or returned by MCP tools.

A public deployment must publish a privacy policy that accurately discloses the
specific synchronized fields, retention period, subprocessors/hosting, account
deletion route, support contact and OAuth identity fields used by that deployment.
This repository document is the technical data contract, not a substitute for the
operator's legally applicable public privacy notice.

## Run memory

History retention ("Don't keep after session", or a number of days) deletes raw evidence. Just before retention removes a session, OpenWorkGraph keeps one small record per run in it, so repeated-workflow and similar-run features still have something to learn from:

- **What a record holds:** the run's hashed family, structural step tokens, the agent's reported end status, timing, approval points, and for agents the work summary: commands, git/gh operations, test outcome, file and line counts, and tokens.
- **What it never holds:** titles, URLs, paths, prompts or tool content. Native session and run identifiers are stored only as keyed hashes, and the key never leaves this computer.
- **When it is kept:**
  - Kept when **retention** removes a session: ephemeral close, expiry, or crash recovery.
  - Deleted when **you** delete a session or a date range: its records go too.
- **Retention and switch:** memory has its own retention (default 90 days) and an on/off switch in History. Turning it off deletes all of it. Since v0.118, the **Keep my history** choice in the Privacy tab sets it together with history retention: **This session only** turns run memory off (keeping nothing after the session), and a number of days keeps run memory for the same number of days.
- **Where it stays:** run memory itself stays on this computer. The Gateway connector can separately synchronize explicitly opted-in visible agent-session messages through their own content channel; that does not synchronize run-memory records.
- **AI access:** MCP reads it only through procedural memory, which requires saved-history AI access to be set to "All saved history".

## Outcome tracking (off by default)

When you turn on "Did agent work hold up?" in History:

- **What is kept:** for a pull request an agent opened (`gh pr create` or a create-pull-request tool), only its host, owner, repository and number. They go to a local watch list keyed to the run, never into stored events, and are never synced.
- **What is contacted:** every 10 minutes your local `gh` CLI (your own login, read-only) is asked for the PR's state and CI result.
- **What each run gets:** a content-free `delivery_outcome`: PRs opened, merged, closed without merge or open, and the CI result.
- **When the link goes:** once the PR is merged or closed with final CI, or after 30 days. Only the outcome counts remain.
- **Errors:** stored as codes (`gh_failed`, `timeout`, `gh_not_found`), never as text.
- **Turning it off:** stops every GitHub call and deletes all stored links. Deleting a session or date range deletes its watches.
- **Before the server sees it:** while tracking is off, hooks still pass a PR reference to the local server with the event, and the server drops it. It exists only in memory and, if delivery is delayed, in the short-lived local agent spool.

## Human work around agent runs

- **During and after each run:** OpenWorkGraph lines up human capture with agent runs in time. It records engaged and away seconds by **app category** (terminal, editor, browser, communication, AI assistant, other). It stores no app names or titles for this. When there is no human evidence in a window, it says so rather than reporting zero.
- **Between Claude Code turns:** the hook compares keyed hashes of the files git reports as changed at the end of a turn and at the start of the next one. The next turn's start records only counts: files changed, how many of them the agent had edited in the last 24 hours, whether HEAD moved, and the gap.
  - **Where the snapshot lives:** in a local state file keyed by the project's hash, for at most 7 days.
  - **When it runs:** only while OpenWorkGraph holds a recording lease. Git runs with its fsmonitor disabled and without taking the index lock.
  - **Turning it off:** `OWG_AGENT_REWORK=0`.

## Screenshots

Screenshot capture exists as disabled code/config support but is disabled in the normal configuration and is not part of the current product direction or standard exports. The current privacy strategy assumes useful workflow inference should work without continuous screenshots or recording.

## Titles: kept, with sensitive details tokenized (v0.108)

Window and tab titles, button and control labels and URL paths carry most of the context that makes work evidence useful. Before anything is stored, OpenWorkGraph keeps their text and replaces the personal details it detects with stable tokens. The same rule applies to browser tabs and desktop apps.

| Captured | Stored |
|---|---|
| `Re: Contract renewal Q4 - Anna Svensson <anna.svensson@acme.se> - Gmail` | `Re: Contract renewal Q4 - PERSON_x <EMAIL_y> - Gmail` |
| `Erik Lund (DM) - Kinvectum - Slack` | `PERSON_z (DM) - Kinvectum - Slack` |
| `Order 4471-2231 – Call +46 70 123 45 67 - Zendesk` | `Order 4471-2231 – Call PHONE_w - Zendesk` |
| `Patient 19850312-1234 remiss - Journal` | `Patient PERSONNUMMER_v remiss - Journal` |
| `Q4 pipeline - Customer tracker - Google Sheets` | unchanged |
| `Acme Logistics AB \| Account \| Salesforce` | unchanged |

- **Tokenized when detected:** people's names, email addresses, phone numbers, personal identity numbers, IBANs, payment cards, credentials and explicitly labelled personal IDs.
- **Kept:** subjects, document and project names, company and product names, business references (order, invoice, PR numbers), dates and amounts. Known SaaS object-ID positions in browser paths are additionally governed by the v0.114 browser-context profile above.
- **Same person, same token:** tokens are keyed to this installation. Once a name has been seen next to an email address, both map to the same person.
- **Best effort, not a guarantee:** names are recognised from a name lexicon and context cues. Unusual or all-lowercase names can be missed, and a company that is also a surname can be tokenized. Browser events also keep the recognised work surface (Gmail, Salesforce, …) for grouping.
- **Existing data:** after upgrading, evidence stored before v0.108 is updated once in the same way, in checkpointed batches (only rows where a personal detail is found are rewritten). Small histories finish during start-up; a long one continues in the background. Until it finishes, AI context is served as Redacted even if you chose Full, and exports are redacted; Full returns automatically afterwards. Older versions had already cut browser titles down to the site name, and that detail cannot be restored. Rows already sent to an organization Gateway are not changed there.

## Storage-time sensitive-identifier hardening

Before event evidence is written to local persistence, the sensitive-identifier sanitizer pseudonymizes or removes high-confidence values whose literal form is not needed for workflow analysis.

Current classes include:

- Swedish personal identifiers
- explicitly labelled patient/journal/case/account identifiers
- IBANs
- recognized payment-card numbers
- credential-shaped secrets
- secret-bearing environment variables/config assignments
- bearer tokens
- passwords embedded in connection strings
- common API/token formats
- full PEM private-key blocks

Tokens are installation-local stable pseudonyms such as:

- `PERSONNUMMER_x`
- `PATIENT_ID_x`
- `CASE_ID_x`
- `IBAN_x`
- `PAYMENT_CARD_x`
- `SECRET_x`

A long Luhn-valid number that is sensitive-looking but cannot be confidently classified as a card can be masked as `SENSITIVE_NUMBER_x` rather than being exposed or falsely described as a card.

## Swedish OCR/reference numbers

Luhn validity alone is not sufficient evidence that a number is a payment card. Swedish OCR references and many other business identifiers also use check digits.

OpenWorkGraph therefore gives explicit non-card/reference cues precedence over card heuristics. Context such as `OCR`, `OCR-nummer`, `referensnummer`, `betalningsreferens`, invoice/customer/tracking cues and similar language prevents a number from being falsely represented as `PAYMENT_CARD_x`.

For old rows that were already irreversibly pseudonymized as payment cards in an earlier version, v0.46+ can repair the semantics when the surrounding OCR/reference cue survives. Such a token can be presented as `OCR_REFERENCE_x`; the original digits cannot be reconstructed.

## Three layers: raw, redacted, safe allowlist

| Layer | What it is | Who sees it |
|---|---|---|
| **Raw** | Rich local evidence as stored: full titles and labels, with detected names and other sensitive details already tokenized (see "Titles" above). | Local analysis (patterns, findings, work profile). Never sent to AI by default. |
| **Redacted** | The original text with only sensitive spans replaced by typed stable tokens (`PERSON_…`, `EMAIL_…`, `PHONE_…`, `PERSONNUMMER_…`, `ID_…`): `Re: Contract for PERSON_1A2B3C - Gmail`. | **Default for every Context MCP tool**, exports (**Redacted**, on by default), and since v0.117 the page/window title in the dashboard **Evidence** table. The dashboard runs this pass again for display and fails closed (shows only the tool name) if it cannot. |
| **Safe allowlist** | Only known safe phrases; everything else dropped. | Dashboard glance views (summary, repeated-workflow steps, work profile, Evidence actions; URL paths and raw button labels are never sent to the dashboard) and organization (Gateway) sharing. |

The **AI context detail** setting (Connect tab → Connections) chooses what AI apps get: **Redacted** (default) or **Full** (labels and titles as stored). Since v0.108, stored titles already have detected names and contact details tokenized, so Full mostly differs from Redacted by skipping a second redaction pass. Detection is best effort, so Redacted remains the recommended setting. An organization can force Redacted. Every MCP response carries `detail_level` so the AI knows which it received. The AI setting never modifies the database. See [OWNER_REDACTION.md](OWNER_REDACTION.md#contextual-redaction-for-ai-context).

## Person/owner presentation redaction

Person-name handling is a separate presentation policy rather than the same storage sanitizer.

When data is returned through dashboard/API/MCP/export surfaces, high-confidence people can be shown as `PERSON_x`, ambiguous first names as `PERSON`, and the local user as `OWNER`.

Persistent person-name learning is intentionally conservative. Strong evidence such as `Name <email>`, sender/recipient fields, `from`, `cc`, `bcc`, `reply to`, `meeting with`, `call with`, or another person-specific field can teach an alias.

Generic workflow language and status/team phrases are rejected. A phrase such as `Transition to In Progress` or `Meeting with Legal Team` should not create a persistent fake person.

See [OWNER_REDACTION.md](OWNER_REDACTION.md) for details.

## Reset learned person aliases

The dashboard exposes this as **Settings → People OpenWorkGraph has learned → Reset learned names** (before v0.117: **Reset learned person aliases** under Organization), backed by:

```text
POST /v1/privacy/reset-learned-names
```

This control is authenticated in v0.48. It deletes only the local learned-person registry. It does not delete captured work history or alter task/event timing.

## Deliberately retained workflow context

OpenWorkGraph is not designed to erase every business fact. The following may remain in rich local evidence when observable because they can be necessary for process understanding:

- company/customer names
- project/deal names
- order/reference numbers
- amounts
- document/page/window titles
- safe UI labels

This is a deliberate utility/privacy tradeoff. A rich export should be reviewed before it is shared beyond the intended analysis context. Browser business-object IDs/locators are separately governed by the v0.114 browser-context profile rather than assumed safe merely because they are business references.

## Excluded applications/pages ("Never record")

Configured excluded applications/title patterns and excluded browser contexts are handled specially. Excluded rows can retain timing/activity evidence while omitting sensitive content labels/titles.

Since v0.118 the lists are visible and editable in the dashboard (**Privacy → Never record**: apps, websites, and words in a window title). They are stored in the local `config.json` (`excluded_apps`, `excluded_browser_host_patterns`, `excluded_title_patterns`). The desktop recorder reads them when it starts, and the server applies them again when desktop activity arrives, so a change takes effect immediately without restarting the recorder. Changes apply to new activity; already recorded activity is not rewritten (use **Delete recorded activity**). The default list (1Password, Bitwarden, KeePass, Keychain Access; titles containing password, private, incognito or bank) can be restored at any time.

This lets broad effort timing remain useful without preserving content from deliberately excluded surfaces. v0.114 checks these exclusions before a Rich enterprise resource locator enters the browser retry queue and again at server ingest.

## Browser URL handling

Browser evidence removes query strings and fragments. Token-like or identifier-like path segments are normalized where possible. Authentication/recovery/invite/token-related path segments receive additional sanitization. Since v0.114, known Google Docs/Drive, GitHub, Salesforce, Jira and Linear object-ID route positions are also masked in Privacy-first and Context modes; a separately validated provider locator is available only when the Rich enterprise locator switch is explicitly enabled.

## AI access switch

AI access is the single gate for every MCP read. It is **off on a new install**, and every MCP tool call checks it live. Since v0.118 the person's choice is remembered across restarts (stored as `ai_access.json` in the local auth directory with owner-only permissions). **Turn AI access off every time OpenWorkGraph starts** (Privacy tab, Advanced view) restores the earlier per-run behavior. If the state file is missing or unreadable, access stays off. Saved-history access is a separate grant: it may expire within 24 hours, or remain active until explicitly revoked when the user selects that option. Turning off AI access revokes saved-history access as well.

## MCP trust boundary

Observed titles and labels are data, not instructions to the AI.

Before results cross the MCP boundary, OpenWorkGraph applies additional output hardening such as:

- removal of invisible direction/control characters
- scalar length limits
- suppression of common command-like prompt-injection text
- `_openworkgraph_security` trust annotation

The HTTP MCP endpoint is also capability-protected in v0.48. Local clients such as Cursor receive the bearer capability through the authenticated dashboard connection flow; stdio clients such as Claude Desktop can launch the local secured stdio entrypoint directly.

This is distinct from persistence sanitization. It is intended to reduce the risk that untrusted observed UI text is treated as an instruction by an agent while preventing an unauthenticated localhost process from using MCP as an alternate read path.

## Raw does not mean unsanitized

The phrase **raw local evidence** refers to the richest persisted source-event layer. It does not mean a byte-for-byte copy of everything visible on screen.

High-confidence secrets/identifiers are already hardened before persistence, and intentionally excluded content is omitted. The raw layer is “raw” relative to OpenWorkGraph's normalized/context derivatives.

## Limits

No heuristic privacy system is perfect. False positives and false negatives remain possible, especially in arbitrary visible titles/labels. Enterprise use should therefore add organization-specific policy, encryption at rest where required, RBAC, retention, auditability, endpoint controls and managed deployment appropriate to the environment.

The current prototype's design goal is a useful local workflow dataset with substantially less invasive collection than screenshots, typed-text capture or recording, plus explicit local authentication rather than relying on loopback binding alone.

## Native agent session files and visible-message continuity (v0.109)

OpenWorkGraph can optionally read local Claude Code and Codex session files. The sensor is **off by default** and first activation is new-only: existing files are primed to their current end rather than retrospectively ingested.

Structural projection remains content-free. The parser recognizes and discards thinking/reasoning and tool-result records; raw native records are never persisted. Tool inputs are inspected only in memory long enough to derive the existing allowlisted structural detail, after which raw arguments are discarded.

Visible user/assistant messages are a separate opt-in channel and never enter the canonical `events` table. Before insertion, OpenWorkGraph applies its high-confidence identity learner and contextual presentation redaction; failure drops the message rather than storing an unprocessed fallback. Name detection remains best effort, so message content should still be treated as sensitive.

AI read access, retention and Gateway sharing are separately controlled. Gateway sharing requires endpoint opt-in **and** organization policy, uses dedicated `agent-sessions:write` / `agent-sessions:read` scopes, and does not retrospectively upload messages captured before opt-in or during a sharing pause. Existing device credentials are not silently granted `agent-sessions:write` after upgrade; endpoints enrolled before v0.109 must be explicitly re-enrolled/rotated before transcript upload is possible.