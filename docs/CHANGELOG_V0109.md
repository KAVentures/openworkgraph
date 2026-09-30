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
