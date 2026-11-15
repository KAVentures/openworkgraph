# Domain-pack mapping validation

Installing a domain pack checks each selected object type and activity against
the selected saved log. The lookup now returns only names actually supplied in
the mapping, rather than materializing every distinct name in the log. Mapping
values are bound SQL parameters and exact, case-sensitive matching and log
isolation are unchanged.

The database still scans or filters its stored data as needed; this change does
not claim a particular end-to-end latency or increase the dashboard's 1,000-name
selection limit. A synthetic 100,000-distinct-name test covers high-cardinality
inputs, literal SQL-like labels, missing names, and a label in another log.
