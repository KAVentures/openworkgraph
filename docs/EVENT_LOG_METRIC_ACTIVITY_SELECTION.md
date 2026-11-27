# Exact activity selection for case metrics

The case-metric builder suggests up to 500 frequent activity labels. For counts, time-between metrics, numeric activity filters, stage boundaries and activity comparisons, enter an exact label or use **Find labels** to search the current case notion. Exact entries override the suggestion and preserve literal spaces and Unicode. Choosing another suggestion clears the exact entry. Labels are limited to 200 Unicode characters; an overlong entry is refused rather than being treated as an empty optional filter.

When editing a saved or draft metric, an activity outside the current suggestions remains editable as its exact label. It may no longer occur in the selected data; the calculation retains its normal missingness and coverage behavior rather than treating a remembered name as evidence of current occurrences.
