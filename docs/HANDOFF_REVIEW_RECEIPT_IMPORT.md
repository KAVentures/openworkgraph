# R5: Import a digest-bound handoff review receipt

Open `dashboard/handoff_review_workspace.html` in a secure local browser context. Load the exported OWG handoff packet and choose **Review evidence**. Record blocker dispositions and export a review-only JSON receipt. To continue later, reload the same packet, review it again, and choose **Import previously exported review receipt**. The dispositions are restored only when the receipt matches the exact SHA-256 bytes of the selected packet and the exact idea, revision, project, specification and ordered blocker set.

A changed packet, changed blockers, malformed JSON, non-review-only flags, missing authority-denial flags, or a file larger than 256 KiB is refused. The receipt import does not verify its author or authenticate the reviewer; it is an unsigned local note. The workspace makes no network calls, does not persist the receipt, and grants no source read, owner approval, consent, or execution authority. The user must separately refresh server-authoritative evidence and permissions before agent handoff.

Acceptance requires an actual browser round-trip (export, reload, import), altered-packet refusal, concurrent selection changes, keyboard accessibility, and the full native CI matrix; source contract tests alone do not establish these gates.
