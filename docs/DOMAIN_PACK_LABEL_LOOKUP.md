# Find a domain-pack mapping label

For any domain-pack role, open **Find a label**, enter part of a label in **Search labels**, and choose
**Find labels**. Results are exact, case-sensitive substring matches from the
selected local log. Select a result to fill the exact-label field, then install
the mapping. **More labels** moves through the next page; each page contains at
most 100 labels. Changing the search clears old results, including responses
still in flight. Searches are not sent to the source system.

The dashboard-session-only `POST /v1/event-logs/{log_id}/domain-pack-labels`
accepts in its JSON body `kind=activities` or `kind=object_types`, a 1–200-character `search`,
and optional `after` cursor returned as `next_after`. Search text is sent in the body rather than the URL. Matching is literal, not
SQL wildcard or regex syntax. Results contain labels only, are ordered by label,
and include only lengths supported by the mapping contract. Lists may change
between pages if the saved log changes; no cross-request snapshot is claimed.
The endpoint is no-store and rejects AI bearer access, like the existing saved
log workspace. Labels may be sensitive source data: the existing local log
retention and dashboard-session boundaries apply.

Finding a label does not establish its business meaning, verify log completeness,
or approve execution. The owner still selects the role and installs the pack.
