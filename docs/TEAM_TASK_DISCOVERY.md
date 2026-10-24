# Team-level task discovery

Roadmap #326 A4. It finds ordered app sequences that several people in a team
repeat, and the sequences that recur independently in more than one team. It
uses only evidence people chose to sync to the gateway. It extends the
organization-level manager insights (`GET /v1/admin/manager-insights/{org}`)
and reuses the same motif extraction.

## Endpoints
- `GET /v1/admin/team-insights/{organization_id}?since=&until=&team=`:
  admin authentication. The `team` parameter limits the report to one team.
- `GET /v1/human/aggregate/team-patterns?since=&until=`: OIDC. It requires
  `aggregate:read`. Callers with `org:evidence:read` see every team; everyone
  else sees only the teams in their `team:<id>:evidence:read` scopes. It uses
  the configured `OWG_GATEWAY_AGGREGATE_MIN_ACTORS` floor (default 5).

Both endpoints write an audit entry. There is no actor filter.

## Privacy limits
- **k floor per cell.** A team pattern appears only when at least k distinct
  team members contributed (k ≥ 3). Teams with fewer than k members are
  listed in `suppressed_teams`, with no counts.
- **Complete weeks only.** Windows snap to complete UTC ISO weeks. The default
  is the last four. The current partial week is never shown.
- **Membership is not retroactive.** A person's work counts for a team only
  from the day after their current team assignment. Changing someone's teams
  restarts that date. Assignments made after the window ends do not count.
- **Rounding.** People and session counts round to the nearest 5, and are
  reported as the floor below that. Durations round to 15 minutes. Comparing
  a team with the organization total therefore does not recover a suppressed
  cell exactly.
- **What is never returned.** Actor, device or session identifiers, titles,
  URLs, message text, and unknown app or site names (these become per-report
  aliases).

These measures reduce disclosure risk. They are not anonymization or
differential privacy. An administrator who repeatedly regroups people can
still narrow results down, which is why team reads are audited. Patterns are
navigation candidates, not verified tasks, and they never claim time savings.
