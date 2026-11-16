# Exact labels in case-filter rules

The case-filter suggestions contain up to 500 frequent activities from the
current analysis. An activity outside that list can still be used in a
follower rule: enter its complete label in the corresponding exact-activity
field. Exact text takes precedence over the suggestion. Choosing a suggestion
again clears that field; clearing the exact field returns to the selected
suggestion.

Labels are compared literally, including leading and trailing whitespace and
Unicode characters. They are limited to 200 Unicode characters, as in the
cohort API. A mistyped or unknown label can match no cases; the editor does not
verify labels by searching the source. Saved filters retain the exact rule and
can be reopened even when the label is outside the current suggestion list.

To find a label, enter one or more characters in an exact field and choose
**Find labels**. Search is case-sensitive and literal; select a result and
choose **Use label** to copy its complete text into the rule. Each page has at
most 100 labels; **More matches** advances through the live results. Search
uses the selected log's current case notion and applied activity mapping, but
not the active case filter, so an empty current result cannot hide all labels.
The lookup may change between pages as the log or mapping changes.

The owner-only lookup uses a POST body at
`/v1/event-logs/{log_id}/activity-labels/search`, returning labels and a cursor
without source event rows. Responses are `no-store`. The query materializes the
existing case-event projection and scans matching labels; the returned page is
bounded, but query time and database memory are not constant in log size.
