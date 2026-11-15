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
