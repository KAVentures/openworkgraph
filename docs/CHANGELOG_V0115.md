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
