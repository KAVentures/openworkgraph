# OpenWorkGraph v0.32 — nontechnical testing

The easiest test path on macOS is the standalone GitHub Release ZIP.

## Start OpenWorkGraph

1. Download `OpenWorkGraph-macOS.zip` from the latest GitHub Release.
2. Unzip it.
3. Right-click `START_OPENWORKGRAPH.command` → **Open**.
4. Confirm **Open** if macOS asks.
5. Approve Accessibility/Input Monitoring if requested.
6. The local dashboard should open automatically.

No manual Python installation or Terminal command setup is required.

## Add the browser sensor

1. Double-click `ADD_BROWSER_SENSOR.command`.
2. Finder opens the correct `browser_extension` folder.
3. In Chrome/Edge, enable **Developer mode**.
4. Click **Load unpacked** and select the opened `browser_extension` folder.
5. The OpenWorkGraph dashboard should show the browser sensor as connected and should report the current extension version.

The browser sensor is important: without it, OpenWorkGraph can see the browser application but cannot reliably distinguish Gmail, Google Docs, Salesforce and other individual browser tools.

## Core checks

### 1. Brief navigation must appear immediately

Open a new tab, type a URL, press Enter, stay on the page for only 1–2 seconds, and switch away **without scrolling or clicking**.

Expected: Raw activity evidence / browser semantic activity contains the visited site. The visit must not depend on a later scroll or click.

### 2. Browser work surfaces should stay separate

Use several browser tools such as Gmail, Google Docs, ChatGPT or another web app.

Expected: work-surface views attribute effort to the logical browser surface rather than collapsing everything into `Google Chrome`.

### 3. Rich raw evidence remains understandable

Work normally for a few minutes.

Expected: Raw activity evidence should make it possible to reconstruct what the observer actually saw. The v0.32 context/operational layers must not destroy or replace the raw source evidence.

### 4. Repeated completed tasks should not equal repeated navigation

Bounce between two websites several times without completing a meaningful action.

Expected: this may appear as a navigation fragment, but it must not become a repeated completed task merely because the same site sequence occurred more than once.

Then perform a genuinely repeatable completed task twice, such as composing and sending two fresh emails.

Expected: separate task executions should be inferred, and the repeated task family should count both executions.

### 5. Delivery should survive temporary interruption

With OpenWorkGraph running, briefly interrupt the local API/observer process or otherwise create a short delivery failure, then restore it.

Expected: queued desktop/browser observations should be delivered after recovery rather than silently disappearing. The dashboard should expose backlog/connection state rather than pretending capture was complete.

### 6. Exports

Capture a few minutes of work, then export the session as JSON and XLSX.

Test both:

- normalized/operational export without rich raw evidence
- deliberate export with rich raw evidence included

Expected: the normalized representation keeps task/process structure while dropping unnecessary content; the rich export preserves enough evidence for detailed reconstruction.

## Useful AI test

Give the exported session to ChatGPT, Claude or another model and ask:

> What repeated work do you see? What appears inefficient? What internal tool or automation would help most? Show the observed evidence behind each suggestion.

The goal is not that OpenWorkGraph itself must make every conclusion. The goal is to produce a sufficiently accurate, reconstructable work dataset that another AI can reason over it.
