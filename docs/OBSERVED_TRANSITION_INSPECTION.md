# Inspect an observed transition

Open a supplied event log or explicitly retained snapshot and choose
**Explore all observed transitions**. Select an arrow, or Tab to its
**Inspect observed transition …** target and press Enter or Space. Wide
transparent stroke targets make thin arrows selectable without altering
frequency/elapsed styling; keyboard targets have a visible focus outline.

The transition inspector shows recorded adjacency count, distinct-case support,
median and maximum elapsed gaps, and buttons for displayed supporting variants.
Support uses each variant's full transition statistics, including transitions
past its 24-event path display limit. If supporting variants were omitted from
the bounded response, the inspector says so; absence from the preview is not
absence from the full log. Node and transition inspection remain separate.
Selecting a node clears transition details, and selecting an arrow clears the
node inspector. Selection does not read providers, save a model or enable an
agent. Existing design/projected maps retain their own semantics and controls.

For retained logs, **Browse retained cases for … → …** queries every case
containing that exact adjacent pair, including cases beyond preview examples.
The query joins consecutive stored positions within the same case; two activity
labels occurring somewhere in a case are insufficient. Repeated occurrences
contribute to transition frequency but never duplicate the case listing.
Pagination preserves the transition and existing date/activity/variant/
participant/team cohort filters. Each case opens its complete event history,
including events performed under other labels or outside a case-date window.
Older case/event responses cannot replace a newer drill-down or a changed log.

Adjacency follows the retained timestamp ordering with supplied row order for
ties. Tied-time order remains unverified, including when it supplies the selected
pair. Arrows and elapsed gaps do not establish causal dependency, business
policy, human handoffs, active labor or verified waiting. This inspection does
not broaden case identity beyond the selected source/import.

## API

The existing dashboard-only `GET /v1/process-mining/datasets/cases` accepts
optional `transition_from` and `transition_to` together. Both must be nonempty
literal labels of at most 64 characters. One-sided pairs and invalid bounds are
rejected. Values are SQL parameters; the same-case, next-position relation uses
the existing event primary key and adds no schema or derived event index.

A filtered response includes `transition_filter`, `transition_basis` and
`tied_timestamp_order_verified: false`. Its original dataset ordinal, case
metadata, cursor and retention/access controls are unchanged. All active
cohort filters are additional AND conditions. Successful responses use the
existing `Cache-Control: no-store` contract. Ordinary AI bearer tokens cannot
read retained cases; no new grant, source read, model request or business write
is introduced.

Verification covers adjacent versus nonadjacent/reversed pairs, repeated
transitions, self-loops, full paths beyond event and case preview caps, case
paging, combined date/activity filters, tied order, invalid/partial parameters,
read-only storage, authentication and keyboard/mouse callback identity. A real
browser flow selects an arrow in a 4,550-event saved log, inspects its 1,505
occurrences across 35 cases, and pages through case 35's complete 130-event
history. Pending case requests are also checked against a newer selection.
