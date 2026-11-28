# Coordinate a departmental AI implementation campaign

The existing automation portfolio remains the system of record. Open
**Coordinate a departmental implementation campaign**, load the current local
portfolio, declare a department/scope label, business objective and success
criteria/measurement plan, then select one to fifty exact saved opportunities.
Filter by existing team labels or search titles, owners and IDs. Filters
are navigation, not department membership verification; only checked ideas
enter the campaign. Freeze the loaded inventory's revisions and download the
campaign baseline JSON. This is a portable local file, not a new portfolio,
server-side campaign record, or authenticated audit log.

Reopen the baseline later, load the current portfolio and compare. Each
original member remains present even when missing from the current inventory.
The review reports current state, revision, owner, team, quarter and
dependency links, flags changed revisions, unexpected conflicting revisions
and missing ideas, and shows current dependencies inside and outside the
campaign. A waiting idea has a nonimplemented dependency and is itself
neither implemented nor rejected, consistent with the existing portfolio.
The snapshot time is the browser's inventory load completion time, not
a server-signed time. Refreshes invalidate the comparison, not the baseline.
Download the comparison or open any current idea in its existing detail view.
Start a new campaign if membership or declared scope needs to change.

The campaign uses the existing authenticated portfolio endpoint and adds no
write endpoint, new storage, model call, execution or approval. Imported
baselines are structurally validated but not independently authenticated.
Recorded states and changed revisions do not establish deployment, causality,
goal achievement or realized savings. Evaluate declared success criteria
using the existing retained quality-benefit and forecast-review functionality.
No new aggregation of modeled benefits or budgets is introduced.

Validation: run `node --test tests/js/ai_implementation_campaign.test.mjs`.
The existing integrated browser regression additionally checks a real saved
idea, frozen campaign baseline and changed revisions after assessment. The
isolated browser smoke used a synthetic portfolio and substituted navigation
and data-read callbacks. Full integrated application and customer acceptance
remain required.
