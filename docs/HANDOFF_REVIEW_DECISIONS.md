# Local handoff review decisions (R5)

Open `dashboard/handoff_review_workspace.html` in a browser. Load a valid exported OWG handoff review packet and optionally a comparison packet. The reviewer sees each recorded blocker and chooses **Needs current evidence**, **Escalate to owner**, or **Not applicable — explain separately**. All dispositions are required to enable **Export review-only receipt**.

The downloaded `owg-review-decisions.json` is a local, non-authoritative note, pinned by SHA-256 to the **exact bytes** of the reviewed packet (candidate when supplied, otherwise baseline). It includes idea/project revisions, dispositions, and explicit false authorization/consent/freshness flags. It does not contain source data, credentials or a verified identity. Changing file selection clears the decisions; concurrent reads cannot repopulate obsolete selections.

**Never** use this receipt to grant source reads, agent execution, business writes or tenant access. Reviewers must independently verify current server consent, revocation, ownership, retention, project revisions and tool permissions before any separate customer-controlled execution. A disposition of “not applicable” is not a waiver of a server-side blocker.

Validation: `python -m unittest tests.test_handoff_review_decisions`; existing handoff browser acceptance and native CI are additionally required. Browser SHA-256 requires a secure context; export fails closed if unavailable. Real consenting-user acceptance is not established by these tests.
