# Local handoff evidence review workspace (R5)

Open `dashboard/handoff_review_workspace.html` in a modern browser. Export an `openworkgraph.evidence-handoff-review.v1` JSON packet from the authenticated OWG automation review journey, then select it as the baseline. Optionally select a second packet to compare revisions and recorded blockers. Select **Review evidence**.

The workspace parses JSON locally (256 KiB per file), validates the explicit review-only authority flags and required revision/digest fields, renders content with text nodes, and shows a comparison of idea/process revisions, hashes and blockers. No network requests, persistence, provider reads or business writes occur. The native file inputs, button, headings and live status are keyboard-accessible.

**Limits:** This is a local review UX milestone, not an authenticated server-side approval or execution workflow. It does not verify the current source state, active consent, tenant membership, owner approval, revocation, retention, execution permissions or actual business outcomes. Even a packet with no recorded blockers remains human-review-only. Return to the authenticated OWG journey to refresh evidence. Never use this tool as an agent execution authorization token.

Run focused checks with `python -m unittest tests.test_handoff_review_workspace`. Real-browser accessibility, full native CI, and consenting-customer acceptance are separate release gates.

Comparison is limited to the same idea and project identity; mismatched packets are refused. The comparison reports changes to dataset-origin content, truncation, reported specifications and source-check export metadata in addition to process/project revisions and blockers. Changing selected files or starting a newer review invalidates unfinished asynchronous reads, preventing mixed snapshots. Matching local snapshots still do not establish current server state or authorization.
