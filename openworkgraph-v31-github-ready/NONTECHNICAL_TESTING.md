# v31 testing

This build is a deliberate rollback to the v27 rich-evidence capture/data behavior. The dashboard should again show detailed Raw activity evidence. Session exports include raw local evidence by default; uncheck the export option only if you intentionally want the normalized-only export.

After upgrading, reload the browser extension once. It should report version `1.5.0-v31-rich-rollback`.

# OpenWorkGraph v27 test

1. Stop the previous observer with **Ctrl+C**.
2. Unzip the v27 standalone build.
3. Right-click `START_ON_MAC_STANDALONE.command` → **Open**.
4. Run `OPEN_BROWSER_SENSOR_FOLDER.command` and reload the browser sensor extension once.
5. Work normally.

## The important v27 checks

### 1. Rich capture is back

The local dashboard's **Raw desktop interaction evidence** and browser evidence should behave like v23: it should be possible to understand what the observer actually saw, including useful browser/page context.

### 2. Work surfaces still separate correctly

Gmail, GitHub, ChatGPT, Lovable, Google Docs/Sheets, etc. should not collapse into Google Chrome in the work-surface views.

### 3. Repeated email task test

Send two new emails back-to-back without deliberately waiting or switching away from Gmail.

Expected task layer:

- two separate `Compose and send email` executions
- both with family `email.compose_send`
- **Repeated task families** shows `Compose and send email` with count 2

This should work even before the current Chrome/Gmail focus span is finalized.

### 4. Normalized operational layer retains structure, not content

The raw local evidence may contain richer text. The task labels/family and MCP-facing layer should instead use safe structure such as `Gmail`, `Compose`, `Send`, `GitHub`, `Create repository`, timing and effort.

Typed text and key identities are never collected by the effort counter.


## Test the v27 export

1. Capture a few minutes of work.
2. Scroll to **Export captured session**.
3. Download JSON and XLSX with raw evidence **unchecked** first.
4. Upload the JSON/XLSX to your preferred AI and ask it to identify repeated work, bottlenecks, and automation candidates.
5. Only tick **Include raw local evidence** if you deliberately want to export the richer local evidence; it may contain sensitive content.

## Test brief navigation capture

Visit a new website for only 1–2 seconds and immediately switch away. Do not scroll or click. After refreshing the dashboard, Browser semantic activity should still contain a navigation/page-view event for that visit.

## v27 checks

1. Open a new tab, type a domain in the address bar, press Enter, wait only 1–2 seconds, then return to Workflow Observer. **Raw activity evidence** should show the visited domain even if you never scrolled or clicked.
2. Bounce between two sites several times without completing an action. This may appear under **Navigation fragments (diagnostic only)** but must not create a **Repeated completed task**.
3. Send two fresh emails. Each Send should anchor a separate inferred task, and **Repeated completed tasks** should show the email task with count 2.

