# Numeric case-attribute cohorts

The shared cohort controls accept a case-attribute name and an optional minimum
and maximum. At least one bound is required. The minimum is inclusive and the
maximum is exclusive; only finite numeric stored values match. Names match the
literal case-attribute key, including punctuation and Unicode. These controls
can be combined with the other criteria in the same form.

The controls appear wherever the shared cohort form is used, including case
evidence, comparisons, cohort metrics and derivation. When the case-evidence
browser also has a workspace case filter, both criteria must hold. The workspace
case filter can combine a numeric range and follower rules, and the combination
can be saved and reapplied. It applies to the views listed in that filter panel;
other sections, such as root-cause analysis, still describe the full case notion.
Root-cause evidence uses the same full population as the association.

Where an operation also accepts its own numeric range, ranges on the same
attribute are intersected. If the ranges do not overlap or name different
attributes, the operation asks for one to be changed rather than ignoring a
condition. Cohort comparisons use their own two definitions and do not apply
the workspace filter implicitly.
