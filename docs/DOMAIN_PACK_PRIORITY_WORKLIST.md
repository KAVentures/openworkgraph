# Weighted compliance priority worklist (R23)

Open **Build a weighted compliance priority worklist** in an installed
domain pack. Load installed packs, choose one exact object type and one
to twelve mapped case rules. Assign each selected rule an integer weight
from 1 to 100 and select a threshold within the sum of those weights.
The default is one point per selected rule, not a severity assessment.

A confirmed violation contributes its weight to both score bounds. An
unknown outcome contributes only to the upper bound. Satisfied and
nonactivated outcomes contribute neither. The review counts three
disjoint groups: cases whose confirmed score reaches the threshold,
cases whose confirmed score is below but possible score reaches it,
and cases whose possible score is below the threshold.

Select whether to include the uncertain group. Cases are sorted by
confirmed points descending, possible points descending, then exact
case identifier ascending. This is a declared review order, not an
estimate of risk probability, financial loss, severity, clinical harm
or employee performance. Scores from different rule selections or
object types are not comparable. There is no automatic remediation,
approval, task execution or change to installed rules.

The dashboard supports exact paginated cases and JSON/CSV export of the
current page. Each row lists confirmed and unknown rule indices.
Exported identifiers and labels can be sensitive; share only with
authorized recipients.

## API

Dashboard-session-only GET and POST
`/v1/event-logs/{log_id}/domain-packs/{pack_id}/priority`.

GET lists mapped rules by exact case type. POST example:

```json
{"case_type":"BillingDoc","weights":[{"rule_index":4,"weight":5},{"rule_index":5,"weight":2}],"minimum_confirmed":7,"include_uncertain":true,"limit":50}
```

For the next page, send the same selection with
`expected_basis_sha256` and `after` from the returned `next_after`.
The digest pins the configuration, weights, threshold, uncertainty
selection, and retained event/object/link fingerprint. A changed
basis requires a fresh review. Cursor fields are ordering positions,
not authorization tokens.

The existing compliance engine is reused on the complete case
projection. The review refuses active label transformations, more than
500,000 source rows, and more than two million case-rule outcomes.
No truncated population is reported as complete.

Regression tests cover weighted invoice violations, exact ordering and
unknown-state bounds, stale installation/selection pins, invalid input,
work limits and a Chromium worklist journey. Synthetic fixtures do
not establish operational priority validity or real customer acceptance.
