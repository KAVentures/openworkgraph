# In-place proposal review and paste import

The existing implementation handoff supports pasted integrated proposal JSON
from an external AI, including a single optional Markdown JSON code fence.
The paste action explicitly replaces the selected proposal, clears human-entered
idea details and prior confirmation, and never invokes an AI or creates an idea.
The JSON interior is retained without reserialization. The existing strict
server parser and exact structural reviewer remain authoritative; browser
preflight does not certify JSON semantics, references, or approval.

After successful structural review, expand **Paste or inspect an AI proposal**.
Inspect the actual outcome, claims and bases, steps, exceptions, unresolved
questions, approval requirements, execution environments, required tools and
evaluation plans. Download the exact reviewed proposal if needed.
When the matching original packet is prepared, choose any distinct cited event
to inspect its original packet data alongside the proposal and coverage gaps.
A stale or missing packet never supplies event payloads. Captured and
model-authored strings are rendered as text, not executable markup.
The browser matches pins and membership but does not independently compute the
canonical server digest. Claims and source authenticity remain unverified.

Any input change that invalidates structural review removes the inspector
content and disables its reviewed-proposal download. Preparing a matching
packet can enable citation drilldown without granting any approval.

Validation: four focused Node tests passed and isolated Chromium smoke tested
paste, reviewed claims, exact output download, literal rendering of HTML-like
evidence, and invalidation. The full browser regression was extended to use
paste and the actual server review; it remains unexecuted locally.
