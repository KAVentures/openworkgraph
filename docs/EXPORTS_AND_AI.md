# Exports and AI analysis

OpenWorkGraph can export the current or historical captured workflow data for analysis in ChatGPT, Claude, Excel, Python or other tools.

## Recommended format

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

The original `metadata_json` remains available for detailed/advanced analysis.

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

## Suggested AI prompt

A useful starting prompt is:

```text
Read README_FOR_AI.md first, then analyze the workflow evidence.
Identify repeated work, bottlenecks, handoffs, manual effort and plausible automation/internal-tool opportunities.
For every recommendation, cite the observed evidence that supports it and distinguish observations from inference.
Do not treat foreground time as proof of active work, and do not infer typed content that was not captured.
```

For more conservative analysis, also ask the model to state what evidence would be needed before acting on each recommendation.

## What the export cannot tell an AI

The dataset deliberately cannot reconstruct:

- the exact text a user typed
- the keys pressed
- clipboard contents
- content hidden behind excluded surfaces
- a visual screenshot of the screen
- whether every second of foreground time represented active cognitive work

The model should therefore infer processes from the available sequence/timing/semantic evidence rather than invent missing content.

## JSON compatibility

The JSON export remains the full structured payload. v0.46 changed serialization from pretty-printed to compact JSON to reduce size; it did not intentionally remove the existing top-level structured payload fields.

Consumers should parse JSON structurally rather than depending on whitespace/indentation.

## XLSX overview

The workbook's **Overview** sheet contains a compact data dictionary explaining the central timing fields, privacy tokens, excluded rows and retained-context warning.

## API endpoints

Exports are available from:

```text
GET /v1/export/json
GET /v1/export/xlsx
GET /v1/export/csvzip
```

Query parameters include `scope=current|all` and `include_raw=true|false`.
