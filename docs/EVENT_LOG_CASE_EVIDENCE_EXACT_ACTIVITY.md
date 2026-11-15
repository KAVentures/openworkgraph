# Inspecting cases by exact activity

The case-evidence panel suggests up to 500 frequent activities. Enter a complete
label in **Exact activity to inspect** to find cases containing an activity
outside that list. The exact field takes precedence over the suggestion and
matches literal text, including whitespace and Unicode. An unknown label can
return no cases; it is not verified automatically.

Choosing a suggestion clears the exact field. Likewise, using an **Inspect
cases** action for an activity or transition clears any earlier exact-activity
override so the requested evidence is shown. This applies to the main case
evidence panel and the equivalent inspector embedded in variant coverage.

Use **Find labels** next to the exact field to search before selecting a label.
The search is case-sensitive and literal, returns up to 100 labels per page,
and uses the inspector's case notion and the applied activity mapping. It is
independent of the active case filter, so it can still find labels when that
filter matches no cases. Results are live and may change between pages. Lookup
returns labels rather than source event rows; the existing case-event projection
and database scan still depend on log size.
