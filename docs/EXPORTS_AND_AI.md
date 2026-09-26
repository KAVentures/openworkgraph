# Exports and AI analysis

OpenWorkGraph supports two complementary ways to analyze captured workflow data with AI:

1. **Export and upload** — universal, explicit, and easy to review before sharing.
2. **MCP** — ongoing controlled access for AI clients that support Model Context Protocol.

A third transport, authenticated HTTP MCP, can be started explicitly for network-only clients such as a ChatGPT Secure MCP Tunnel workflow. It is not kept open during normal OpenWorkGraph operation.

## Export: recommended format

For direct upload to ChatGPT or Claude, prefer:

1. **CSV ZIP** — compact, transparent and includes an AI-oriented data dictionary.
2. **XLSX** — convenient when a model/user benefits from workbook structure and manual inspection.
3. **JSON** — preserves the complete structured representation and is primarily intended for code/integrations.

JSON is serialized compactly to reduce file/token overhead without changing its field schema.

## Rich vs normalized-only export

The dashboard option **Include rich raw session evidence** controls whether the richest persisted event layer is included.

- **Enabled:** best for reconstructing detailed work, inspecting labels/pages/actions and asking an AI to explain evidence behind recommendations.
- **Disabled:** better when a more content-minimized operational representation is sufficient.

“Raw” does not mean unsanitized. High-confidence sensitive identifiers/secrets are hardened before persistence, and exports are also passed through presentation policy.

## CSV ZIP contents

A CSV ZIP can contain tables such as:

- `effort_by_surface.csv`
- `transitions.csv`
- `inferred_tasks.csv`
- `repeated_task_families.csv`
- `operational_events.csv`
- `semantic_activity.csv`
- `raw_local_evidence.csv` when rich evidence is included

The ZIP also contains:

- `README.txt`
- `README_FOR_AI.md`

`README_FOR_AI.md` is intentionally written so a model can understand the data before interpreting it.

## Useful event columns

Common semantic fields are pulled out of nested metadata into plain CSV/XLSX columns:

- `action`
- `page_host`
- `page_path`
- `target_label`
- `target_role`

The original `metadata_json` remains available in exports for detailed/advanced analysis.

A blank `target_label` is not necessarily missing capture. OpenWorkGraph deliberately suppresses some long row-like accessibility labels because they can contain an entire email, chat, record or patient row rather than a safe control name. Some applications also expose no useful accessibility label.

## Timing fields

Important fields include:

- `focus_span` — time during which one application/window remained in the foreground
- `foreground_seconds` — total foreground time
- `engaged_seconds` — foreground time with recent input activity; an estimate, not proof of continuous work
- `active_input_seconds` — time very close to observed keyboard/mouse input
- `idle_seconds` — foreground time outside the engagement grace window
- `keypress_count` — number of keypresses only; never which keys or what text was typed
- `click_count` / `scroll_count` — observed interaction counts

## Privacy-token meanings

Common tokens include:

- `OWNER` — the local observer user under presentation policy
- `PERSON` — an unnamed/non-linkable person
- `PERSON_x` — a stable pseudonymized person
- `IBAN_x` — masked IBAN
- `PAYMENT_CARD_x` — recognized masked payment card
- `SENSITIVE_NUMBER_x` — long Luhn-valid number masked without asserting it is a card
- `SECRET_x` — masked credential/secret
- `OCR_REFERENCE_x` — legacy OCR/reference value that an older version had already misclassified; original digits cannot be reconstructed
- `PATIENT_ID_x`, `CASE_ID_x`, `ACCOUNT_ID_x` and similar tokens — stable pseudonyms for those identifier classes

Exact token suffixes are intentionally opaque.

## Deliberately retained context

Amounts, company names, project/deal names, order/reference numbers, document/page/window titles and safe UI labels may be retained because they can be necessary to understand the workflow.

This means a rich export is not automatically suitable for arbitrary external sharing. Review it before sharing outside its intended analysis context.

## Suggested AI prompt for an export

```text
Read README_FOR_AI.md first, then analyze the workflow evidence.
Identify repeated work, bottlenecks, handoffs, manual effort and plausible automation/internal-tool opportunities.
For every recommendation, cite the observed evidence that supports it and distinguish observations from inference.
Do not treat foreground time as proof of active work, and do not infer typed content that was not captured.
```

For more conservative analysis, also ask the model to state what evidence would be needed before acting on each recommendation.

---

# MCP analysis in v0.49+

## AI access is explicit

MCP reads are **OFF on every OpenWorkGraph launch**.

The dashboard's **AI access** switch controls whether MCP tools may read workflow evidence. The server checks that switch on every tool call, so turning access off also blocks an AI client that was already configured or connected.

When access is off, the MCP client receives an intentional error telling the user to enable AI access in the local dashboard.

## Local AI clients use compact stdio

Claude Desktop, Cursor and other new local MCP connections should use the compact local stdio server:

```text
mcp_server.compact_stdio
```

The dashboard generates the correct installed Python path and environment automatically. This avoids keeping an additional localhost MCP port open during normal use and exposes the smaller default MCP tool surface introduced in v0.87.

`mcp_server.secure_stdio` remains available as the **legacy 24-tool compatibility entrypoint** for saved configurations created before v0.87. New setups should not use it unless they explicitly require the legacy tool names.

The v0.49+ release line also includes `OpenWorkGraph-Claude.mcpb`, a small Claude Desktop extension wrapper around the installed stdio server; current bundles use the compact surface.

## HTTP MCP is on demand

Clients that cannot launch a local stdio process can explicitly request authenticated Streamable HTTP from the dashboard.

OpenWorkGraph:

1. selects an available loopback port,
2. launches its own MCP child,
3. gives the child a random launch-time nonce,
4. authenticates to `/openworkgraph-id`,
5. verifies the exact nonce,
6. only then advertises the endpoint to the dashboard.

This means an unrelated process that merely owns port 8788 is not treated as OpenWorkGraph. Port 8788 is only preferred when free; another loopback port can be used.

## Compact MCP surface

New v0.87+ connections expose these default tools:

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

`OWG_EXPERIMENTAL_GOVERNANCE=1` adds the explicitly experimental governance tools documented in `EXPERIMENTAL_GOVERNANCE.md`. The flag controls MCP exposure; it does not remove the underlying governance REST implementation.

For repeated-work feedback, call `find_repeated_workflows` first and pass its exact `family_key` to `how_did_similar_runs_go`. `task_family` is a display/canonical human family such as `email.reply`; procedural-memory `family_key` values are the exact identifiers used to retrieve prior runs.

## Canonical rich-evidence tool

The main evidence retrieval tool is:

```text
get_workflow_trace(
  since=None,
  until=None,
  cursor=None,
  limit=25,
  scope="current"
)
```

The compact server intentionally defaults to a small first page. Increase `limit` when needed, or pass `next_cursor` back as `cursor` until `has_more` is false.

Useful row fields include:

- `observed_at`
- `app`
- `window_title`
- `event_type`
- `action`
- `target_label`
- `target_role`
- `page_host`
- `page_path`
- `duration_seconds`
- `foreground_seconds`
- `engaged_seconds`
- `active_input_seconds`
- `idle_seconds`
- `keypress_count`
- `click_count`
- `scroll_count`
- `source`
- `session_id`

Storage/internal fields that normally do not help the model — such as database id, schema version, device/sensor identifiers, screenshot path and full `metadata_json` — are omitted from the default MCP row.

This is a presentation optimization, not a weaker capture layer. The underlying rich local evidence remains available to OpenWorkGraph.

## Stable pagination

A trace response includes:

```text
returned
total
has_more
next_cursor
snapshot_until
```

When `has_more` is true, the AI passes `next_cursor` back to the next call.

The cursor includes the last `(observed_at, database id)` pair so multiple events with identical timestamps cannot be skipped or duplicated. The first call also freezes a `snapshot_until` boundary, so newly captured events do not move later pages underneath the model.

## Compact derived tools

Summary and task tools intentionally return small derived representations instead of appending hundreds of raw events. `get_current_work_context` also uses a small default evidence page and slim semantic activity. When the AI needs supporting detail it should call `get_workflow_trace` for the relevant time range or request a larger explicit limit.

This design reduces model context consumption and makes truncation less likely while preserving the rich evidence needed for reconstruction.

## AI guide resource

MCP exposes:

```text
openworkgraph://ai-guide
```

It contains the same data dictionary as `README_FOR_AI.md`, including timing semantics, privacy tokens, retained-context warnings and capture limitations.

## MCP activity visibility

The local dashboard shows recent MCP activity, including:

- tool name
- time
- returned row count
- approximate returned bytes
- evidence time range when available
- denied calls while AI access was off

Search text and tool arguments are not included in the default activity log.

## MCP privacy distinction

An export is a file the user can inspect before uploading.

With MCP, an AI client can decide which tool to call after access has been enabled. **Evidence returned by an MCP call is sent to the AI provider so that model can process it.** Keeping the OpenWorkGraph database local does not mean the returned tool data stays local to the computer.

The per-run AI-access switch and local activity view exist to make that boundary explicit.

---

## What neither export nor MCP can tell an AI

OpenWorkGraph deliberately cannot reconstruct:

- the exact text a user typed
- the keys pressed
- clipboard contents
- content hidden behind excluded surfaces
- a visual screenshot of the screen in normal operation
- whether every second of foreground time represented active cognitive work

The model should infer processes from the available sequence/timing/semantic evidence rather than invent missing content.

## JSON compatibility

The JSON export remains the full structured payload. Compact serialization reduces size without intentionally removing the existing top-level structured payload fields.

Consumers should parse JSON structurally rather than depending on whitespace/indentation.

## XLSX overview

The workbook's **Overview** sheet contains a compact data dictionary explaining the central timing fields, privacy tokens, excluded rows and retained-context warning.

## Relevant API endpoints

Exports:

```text
GET /v1/export/json
GET /v1/export/xlsx
GET /v1/export/csvzip
```

Compact trace / MCP controls:

```text
GET      /v1/workflow-trace
GET/POST /v1/ai-access
GET/POST /v1/mcp-activity
GET/POST /v1/mcp-http
GET      /v1/mcp-connection-config
```

All data/control endpoints require the appropriate authenticated local session/capability in normal operation.
