# Variant coverage

The event-log workspace's **Variant coverage** analysis ranks complete retained
case histories by their exact recorded activity sequence. It reports how many
variants are needed to cover 50%, 80%, 90% and 95% of the selected cases. This
helps a process analyst decide whether to investigate a common path or a long
exception tail before designing improvements.

Targets use every selected case and every distinct variant, not a sample or the
top twenty variant rows. The table displays twenty rows by default, with exact
omitted variant and case counts. API callers can request 1–200 rows via
`GET /v1/event-logs/{log_id}/variant-coverage?limit=20`. Existing dashboard-session
permission, case notion and whole-case filters apply. Equal-count variants use
literal sequence order for deterministic ties. Paths longer than 100 activities
are shortened only in the response; exact sequence identity and coverage still
use the entire path, and the original length is reported. Empty populations
have no threshold value rather than a misleading zero-variant success.

JSON downloads retain scope, coverage targets and omission counts; CSV downloads
contain the displayed rows, with cumulative shares and explicit path truncation.
Downloads include activity labels and log metadata, so review before sharing.

Frequency is not automation suitability or employee effort. These paths do not
prove a true business boundary, complete source coverage, or verified outcomes.
No source events, process definitions or permissions change.
