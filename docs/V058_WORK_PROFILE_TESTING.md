# v0.58 work-profile and browser-signal testing

This guide covers the additive v0.58 work-profile intelligence and the two browser metadata signals. Existing capture, pause/stop, deletion, Gateway sync, MCP, and export behavior should continue to work unchanged.

## Work profile

Open **Overview** and find **Work profile**.

Expected:

- fragmentation is described as surface switching/focus structure, not productivity;
- manual transfers are linked copy/cut → paste actions and never contain clipboard contents;
- AI-tool usage reports observed surface time/actions only and does not claim to know prompt contents;
- daily rhythm reports observed timing/gaps and does not assume gaps are breaks;
- navigation/hunting, rapid-click, and authentication-flow rows are labelled as candidates that need review;
- voluntary self-tags are clearly shown as user-provided context.

## Browser metadata controls

Open **Organization → What is captured**.

Expected defaults:

- **Browser performance timing:** on;
- **File upload category:** off.

Changing either switch is stored locally. The paired browser sensor should pick up the setting automatically without requiring new browser permissions.

### Browser performance timing

With the setting enabled, browse several ordinary pages.

Expected: the work profile can show rounded navigation/load timing. It must not contain resource URLs, request bodies, page contents, or typed values from this signal.

### Optional file upload category

Enable **File upload category**, then use a disposable test page to select files for upload.

Expected: OWG records only coarse categories such as `spreadsheet`, `document`, `image`, or `structured-data`, plus a count. It must not record filename, path, exact size, hash, or file contents.

Disable the setting and repeat the action.

Expected: no new `file_upload_category` evidence is recorded while disabled.

## Exports

Test JSON, CSV ZIP, and XLSX from the existing **Export** tab.

Expected:

- existing export entries/sheets remain present;
- JSON includes structured `work_profile` data;
- CSV ZIP includes derived work-profile tables, including manual transfers, AI usage, rhythm, hunting/friction candidates, tool waiting, file-upload categories, and self-tags where data exists;
- XLSX appends corresponding work-profile sheets without removing or renaming existing sheets;
- rich raw evidence remains controlled by the existing export checkbox.

## MCP

Enable AI access for the run and connect an existing MCP client.

Expected: `get_work_profile(scope)` is available in addition to existing tools. AI access still starts off on every run, and calls continue to use the existing per-call authorization and MCP activity audit behavior.

## Privacy regression

The v0.58 additions must not introduce:

- typed text;
- clipboard contents;
- ordinary key identities;
- microphone state;
- calendar contents;
- filenames or file paths;
- exact file sizes or file hashes;
- file contents;
- resource-timing URLs.

These work-profile values are process/context signals for automation and workflow analysis, not employee productivity scores.
