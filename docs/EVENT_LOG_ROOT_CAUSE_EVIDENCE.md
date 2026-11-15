# Inspect cases behind an association

Root-cause results can offer **Inspect cases** for an activity, resource,
case attribute or numeric attribute range. The case-evidence list uses the
exact filter returned with that result, including unrounded numeric boundaries.
Some results cannot offer an exact filter, for example when a displayed
attribute value was shortened; those rows have no action.

Root-cause results describe the full selected case notion, regardless of the
workspace case filter or fields already entered in case evidence. Their evidence
selection does the same, clears the previously selected path and displays its
filter in the evidence panel. It does not change the workspace filter or the
fields. Selecting **Browse cases** again uses those fields and the active
workspace filter. Association still does not establish causation, ordering or a
verified business outcome.

For a numeric range or activity, **Filter views** replaces the workspace case filter with
the exact condition from the association and refreshes the views that support that
filter. Save the current case filter first if you may want to restore it. This
action does not apply to associations that cannot be represented as a numeric
range or exact activity.
