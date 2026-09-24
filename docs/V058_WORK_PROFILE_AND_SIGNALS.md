# v0.58 work profile and browser metadata signals

OpenWorkGraph v0.58 adds a derived local work profile and two bounded browser metadata signals. The design goal is to improve process/automation insight without turning OpenWorkGraph into an employee productivity scoring system.

## Derived locally from existing evidence

The following signals require no new sensor data:

- surface-switch fragmentation and longest uninterrupted surface stretch;
- linked copy/cut → paste transfer patterns using OWG-generated transfer IDs;
- daily timing/rhythm and observed gaps;
- AI-tool surface usage and transfer direction;
- communication-action counts as workload context;
- repeated-resource navigation/hunting candidates;
- rapid-click friction candidates from existing safe click evidence;
- auth-flow duration candidates from existing navigation evidence;
- voluntary fixed-category self-tags.

These outputs are derived, regeneratable hints. They are not ground truth, performance ratings, or employee productivity scores.

## New browser metadata signals

### Browser performance timing

Default: **on**.

Captures only rounded top-frame Navigation Timing values such as response wait, DOM ready, and load completion. Values are rounded to 50 ms and bounded. Resource Timing entries, resource URLs, response bodies, and page contents are not collected.

### File upload category

Default: **off**.

When explicitly enabled, OWG inspects only the browser-provided MIME type of files selected in a file input and converts it immediately to a coarse category such as `image`, `document`, `spreadsheet`, `structured-data`, `archive`, or `other`.

It does not read or retain filenames, file paths, exact file sizes, hashes, file contents, or directory-relative paths.

No additional browser permissions are requested for either signal. In particular, v0.58 does not request Downloads, History, or microphone permissions.

## Controls

Open the **Organization** tab and use the **What is captured** card.

- Browser performance timing can be turned on/off.
- File upload category is off by default and must be enabled explicitly.
- Settings are stored locally and are supplied only to the already-paired browser sensor through the existing authenticated browser context endpoint.

Changing a setting affects future browser events; it does not rewrite historical evidence.

## Work profile UI

The Overview tab gains a **Work profile** section showing factual/derived process signals such as:

- surface switches and focus stretches;
- repeated manual transfers between work surfaces;
- observed AI-tool usage;
- daily rhythm;
- repeated-resource navigation candidates;
- observed browser loading time;
- rapid-click and auth-flow candidates that are explicitly marked as needing review;
- optional coarse file-upload categories when enabled.

The UI deliberately avoids labels such as "productive", "unproductive", "rage click", or "notification-driven interruption" because the observed evidence does not justify those judgments.

## Exports

Existing export filenames and existing sheets/entries remain unchanged. v0.58 appends derived work-profile data:

- **JSON:** structured `work_profile` object.
- **CSV ZIP:** additional work-profile, manual-transfer, AI usage, rhythm, hunting/friction, browser timing, optional file-category, and self-tag tables plus explanatory README material.
- **XLSX:** additional dedicated work-profile worksheets while preserving the existing workbook sheets.

Rich raw evidence remains optional as before.

## MCP

`get_work_profile(scope)` is an additive local MCP tool. It uses the same per-run AI-access switch, authenticated local API, activity audit, and prompt-injection filtering path as the existing secure MCP tools. Existing MCP tool contracts remain unchanged.

## Privacy boundaries retained

v0.58 does not add capture of:

- typed text;
- clipboard contents;
- ordinary key identities/order;
- microphone state;
- calendar content;
- file names, paths, exact sizes, hashes, or contents;
- browser query strings/fragments;
- Resource Timing URLs.

Copy/paste transfer analytics use OWG-generated transfer/link IDs only. Historical clipboard action rows are re-contextualized idempotently so older evidence can participate without introducing clipboard contents that were never captured.

## Suggested manual checks

1. Start OWG and confirm the Overview **Work profile** appears.
2. Switch among several work surfaces and confirm fragmentation/focus values change without any new permissions.
3. Copy on one observed surface and paste on another; confirm a transfer pair appears without clipboard text.
4. Visit several pages and confirm browser load timing appears when performance timing is enabled.
5. Disable performance timing in **Organization → What is captured** and confirm future timing events stop.
6. Confirm file upload category starts off. Enable it, select disposable files in a test page, and verify only coarse categories/counts appear.
7. Disable file upload category and confirm future file-selection category events stop.
8. Export JSON, CSV ZIP, and XLSX and confirm the new work-profile tables are present while the prior export structure remains.
9. Enable AI access and call `get_work_profile`; disable AI access and confirm the tool is denied again.
10. Inspect browser extension permissions and confirm there is no Downloads, History, or microphone permission.

All signals should be interpreted as process evidence to support automation/research, not as individual performance judgments.
