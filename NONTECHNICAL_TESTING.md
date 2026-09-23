# OpenWorkGraph v0.57 — nontechnical testing guide

This is the recommended practical test path for the current public prototype.

## Start OpenWorkGraph

### macOS

1. Download `OpenWorkGraph-macOS.zip` from the latest GitHub Release.
2. Unzip it.
3. Right-click `START_OPENWORKGRAPH.command` → **Open**.
4. Confirm **Open** if macOS asks.
5. Approve Accessibility/Input Monitoring if requested.
6. The local dashboard should open automatically at `http://127.0.0.1:8787`.

No manual Python installation is required.

### Windows

1. Download `OpenWorkGraph-Windows.zip` from the latest GitHub Release.
2. Unzip it.
3. Double-click `START_OPENWORKGRAPH.cmd`.
4. Review/accept the early-prototype security warning only if you trust the repository.
5. The local dashboard should open automatically.

No manual Python installation is required.

## Add the browser sensor

The browser sensor is optional but strongly recommended for browser-heavy work.

On macOS, run `ADD_BROWSER_SENSOR.command`. On Windows, run `ADD_BROWSER_SENSOR.cmd` from the release package and follow the browser instructions.

The sensor is important because desktop observation alone can identify the browser application but cannot reliably separate Gmail, Google Docs, Salesforce, ChatGPT and other web tools.

## v0.57 dashboard checks

### 1. Dashboard should be data-first

Expected on launch:

- a dark top bar with OpenWorkGraph and recording state;
- compact AI / organization / browser-sensor status chips;
- tabs for **Overview**, **Evidence**, **Connect AI**, **Organization**, and **Export**;
- work evidence visible immediately rather than below setup/marketing panels.

Switch tabs and reload the page.

Expected: the active tab is remembered for the current browser session and search/expanded-row state is not reset by the 5-second refresh loop.

### 2. Recording controls must be real

In live mode, use **Pause**.

Expected: recording state changes to `Paused · not recording`; desktop and browser evidence from the paused interval must not later appear after Resume.

Then Resume, Stop, and Start new run.

Expected:

- Resume records only new evidence after resume;
- Stop keeps the dashboard/API running and preserves existing local data;
- Start new run begins a new run scope without reviving the stopped interval.

### 3. Timeline and repeated workflows

Use several work surfaces and complete a repeatable task at least twice.

Expected:

- Overview timeline shows separate work-surface lanes rather than double-counting browser semantic events as extra time;
- browser focus is attributed to the logical site/tool when known;
- repeated completed workflow executions are grouped into one pattern row;
- pattern labels remain suggestions and display `Needs review`/confidence information;
- expanding a pattern shows the individual run windows and evidence boundaries.

### 4. Delete local evidence

In **Evidence**, choose **Delete last 15 minutes**, **Delete last 1 hour**, or a custom range and review the inline confirmation.

Expected:

- the confirmation clearly states that deletion is irreversible on this computer;
- the selected local evidence disappears from the dashboard;
- late desktop/browser delivery from the deleted interval does not recreate it;
- unsent deleted rows are never synchronized later to an organization Gateway;
- evidence that was already synchronized to a Gateway is **not** claimed to be automatically recalled.

Do this only with disposable test evidence.

## Core functional checks

### 5. Brief navigation must appear without a later click

Open a new tab, type a URL, press Enter, stay on the page for only 1–2 seconds, then switch away without scrolling or clicking.

Expected: the site appears in browser semantic/raw activity evidence. Capture must not depend on a later interaction.

### 6. Browser work surfaces should stay separate

Use several web tools such as Gmail, Google Docs and ChatGPT.

Expected: work-surface views attribute effort to logical browser surfaces instead of collapsing everything into `Google Chrome`/`Microsoft Edge`.

### 7. Native controls should be semantic when available

Click ordinary buttons/menus in a native application.

Expected: where macOS Accessibility or Windows UI Automation exposes safe metadata, OpenWorkGraph should record role/label context. Some applications expose less metadata; missing labels by themselves are not necessarily a failure.

### 8. Keyboard activity must be counts only

Type in a normal application for a while.

Expected: keypress counts/engagement increase. OpenWorkGraph must not display or export the actual typed text or key sequence.

### 9. Repeated navigation must not equal repeated completed work

Bounce between two websites several times without completing a meaningful action.

Expected: this may appear as navigation/transition evidence but must not become a repeated completed task solely because the sequence repeated.

Then perform a genuinely completed task twice, such as composing and sending two fresh emails.

Expected: separate task executions can be inferred and a repeated task family can emerge when completion evidence is present.

### 10. Delivery should survive temporary interruption

Create a brief local delivery interruption while OpenWorkGraph is running, then restore it.

Expected: queued desktop/browser observations should be retried rather than silently disappearing. The dashboard should expose connection/backlog state.

## Privacy regression checks

### 11. Swedish OCR/reference numbers must not become cards

Use visible workflow text containing a real-looking OCR/reference number, for example in a test document/title/label where it can safely be observed.

Expected: an explicit `OCR`/reference cue prevents the number from being described as a `PAYMENT_CARD_x` token.

The privacy rule is semantic: Luhn validity alone is not enough to call a number a card.

### 12. Real payment cards stay masked

For a controlled test only, use a standard non-sensitive test card number such as `4111 1111 1111 1111` in a safe local test label.

Expected: it is represented as a masked `PAYMENT_CARD_x` token rather than literal digits in persisted/presented evidence.

Do not use a real card number for testing.

### 13. Secret/config shapes should be removed

In a controlled test document/terminal using fake credentials only, try shapes such as:

```text
DATABASE_PASSWORD=fake-test-password-123
Authorization: Bearer fakeTokenValue123456789
postgres://admin:fake-test-password@db.example.invalid:5432/app
```

Expected: the secret value is replaced with `SECRET_x` while useful surrounding context such as the database scheme/host can remain where appropriate.

Never put a real secret into a test just to verify redaction.

### 14. Status/team phrases must not become people

Expose labels such as:

```text
Transition to In Progress
Meeting with Legal Team
Sprint board: In Progress column
```

Expected: these phrases should remain status/team/process language and should not create persistent `PERSON_x` identities.

### 15. Reset learned person aliases

After the observer has learned a genuine person alias from strong evidence, use **Reset learned person aliases** in the dashboard.

Expected: the local learned-person registry is reset, but captured workflow history and timing remain intact.

## Export checks

Capture several minutes of ordinary work, then test all three export formats from the **Export** tab.

### CSV ZIP

Recommended for direct AI analysis.

Expected:

- the ZIP contains `README_FOR_AI.md`;
- common event fields such as `action`, `page_host`, `page_path`, `target_label` and `target_role` are plain columns when available;
- `metadata_json` remains available for detailed analysis;
- rich raw evidence is included only when selected.

### XLSX

Expected:

- the Overview sheet contains the AI/data dictionary;
- event tables expose the same common semantic fields;
- the workbook can be inspected manually without decoding nested JSON for every common field.

### JSON

Expected:

- valid compact JSON;
- the same structured payload semantics as before; consumers must not rely on whitespace/pretty-printing.

## Useful AI test

Upload the CSV ZIP or XLSX to an AI assistant and ask:

> Read the data dictionary first. What repeated work, bottlenecks, handoffs or manual effort do you see? What internal tool or automation might help? For every recommendation, show the observed evidence and distinguish observations from inference. Do not infer typed text that was never captured.

Good output should use the available sequence/timing/semantic evidence without pretending it saw screenshots or typed content.

## Privacy sanity check before sharing

Rich exports may intentionally retain business context such as amounts, company/customer names, project/deal names, order/reference numbers, document/page titles and safe UI labels.

Expected: secrets and high-confidence identifiers are hardened, but useful business context remains. Review a rich export before sharing it outside its intended analysis context.

## What is not a failure

The following can be expected in the current prototype:

- some native apps expose weak/no safe control labels;
- a long row-like click may have an empty `target_label` because OpenWorkGraph intentionally suppresses oversized mail/chat/record labels;
- engagement time is an estimate, not proof of continuous work;
- the optional browser sensor may require a one-time browser reload/approval after updating;
- local deletion does not automatically recall evidence that an organization Gateway already received.

The goal is a reconstructable, privacy-conscious workflow dataset — not perfect semantic understanding of every application.
