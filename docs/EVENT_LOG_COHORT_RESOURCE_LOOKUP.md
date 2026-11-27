# Find resources for local cohort filters

The resource field in local cohort forms matches a literal retained resource label. Use **Find labels** to search within the current case notion, page through matches, and use one without retyping it. This is available in the case inspector, KPIs, cohort comparisons and metrics, tagging, derived logs and saved-graph comparisons. Search uses the existing dashboard-authorized resource-label endpoint; it does not read new source data or grant sharing access.

Names are matched exactly, including Unicode and punctuation. An unknown label may match no cases; a value longer than 200 Unicode characters is refused. Editing a cohort continues to invalidate any preview or result where that workflow already requires it.
