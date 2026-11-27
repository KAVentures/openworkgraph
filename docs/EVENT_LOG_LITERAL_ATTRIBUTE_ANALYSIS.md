# Literal attribute names in time and metric analysis

Processing and waiting-time analysis accepts retained lifecycle and end-time attribute names containing quotes, backslashes, slashes, tildes and dots. The typed case-analysis builder also accepts these literal names for numeric event and case attributes. Names are looked up as one complete key, not as a nested path or SQL expression. For example, `amount/usd` refers to the attribute with that exact name.

The existing 120-character and control-character limits still apply. CSV import retains selected attribute names as normalized by the importer, so use the stored name shown by the log when selecting an attribute. These analyses retain their existing missing-value, timestamp and coverage rules; this change does not alter stored events or saved analyses.
