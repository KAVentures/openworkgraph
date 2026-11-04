# Source-stage navigation in the evidence journey

The authenticated Automation Pipeline evidence journey offers **Inspect local source workspace** beside its pinned-process and human-review actions. It focuses the existing local event-log workspace for independent inspection of the imported dataset and cohort. The action makes no network request and does not import, refresh, approve, share or execute anything.

A missing workspace is reported as unavailable. A missing, stale or unchecked dataset origin is explicitly described as unverified. A current fingerprint is only a snapshot status, not evidence that the cohort or source is complete. Opening the workspace does not select a matching log automatically; the operator must confirm the source and cohort before taking further action.

Keyboard users can activate the button with Enter and focus moves to the workspace card. Browser regression checks navigation, disclosure and absence of project/source-check writes. Real consenting-user and provider acceptance remain separate gates under #326 and #370.
