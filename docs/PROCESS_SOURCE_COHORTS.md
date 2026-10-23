# Compare complete cases by supplied source alias

After explicitly retaining a [reviewed shared-key import](REVIEWED_SHARED_CASE_IMPORTS.md),
**Filter complete retained cases → Cases with source alias** selects cases
containing at least one event from the chosen `source_1` … `source_4` alias.
The selector uses the full saved snapshot's aliases, including after another
filter reduces its population. Previewing data never silently saves it.
Logs without retained source provenance have a disabled selector.

Apply the filter to inspect the complete population's map, variants, label
support, timings, examples and paginated case histories. All events remain,
including events from other sources. Repeated matching events never duplicate
a case. Date bounds still select by the case's first retained event; activity
presence and exact stored variant are separate whole-case conditions.

When participant, team and source filters are selected together, all selected
metadata labels must occur on the same event. Finding the participant in one
event and the source in another is insufficient. The activity-presence filter
can match another event in that complete case. Activity-label drill-down adds
its own exact activity/label event constraint; it does not claim to identify
the occurrence satisfying every other whole-case condition.

The existing **Compare two complete-case selections** flow accepts source
filters for baseline and current populations after separate comparison review.
It uses all retained events, reports exact same-snapshot case overlap and can
reopen either population. A case involving both sources belongs to both
populations. Timing differences do not establish source performance, employee
effort, independent samples, causal improvement or business outcomes.

## API and boundaries

The existing dashboard-only `GET /v1/process-mining/datasets/cases`,
`GET /v1/process-mining/datasets/cohort` and baseline/current filter objects in
`POST /v1/process-mining/datasets/cohorts/compare` accept `contains_origin`.
Only literal `source_1` … `source_4` aliases are accepted; native values are
rejected with generic errors. Selecting an alias not present yields an empty
cohort; comparison still requires two nonempty selections. A snapshot without
reviewed source provenance rejects source filtering even if a nullable schema
column exists. Existing authentication, expiry, no-store responses, bounds,
read snapshots and review requirements remain in force.

Aliases describe caller-selected export provenance inside one import. They do
not establish provider/account identity, authoritative business case boundaries,
complete source coverage or independent observations. This feature reads the
existing local retained data and introduces no new capture, joins across logs,
source calls, retained selections, AI access, business writes or permissions.

Verification covers whole histories, same-event metadata intersections,
pagination, exact overlap, date/activity combinations, retained key aliases,
read-only behavior, uncollected dimensions, bounded aliases, sanitized API
errors and authentication. The real browser flow filters a mixed-source case,
keeps its other-source events, compares reviewed source populations and checks
mobile layout, deletion and unchanged canonical evidence.
