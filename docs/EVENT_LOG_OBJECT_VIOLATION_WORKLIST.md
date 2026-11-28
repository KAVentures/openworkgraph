# Object-rule violation worklists

After checking object rules, open **Inspect violations** for a rule and choose **Download all violations (CSV)**. The worklist contains every violation for that reviewed rule, including separate event and object keys, the finding, the rule's parameters, and the retained log revision. For supplied-relationship rules it also records the reviewed graph digests and whether a qualifier was explicitly selected (including an empty qualifier).

The download is available to the local dashboard owner. It recomputes the rule against the reviewed revision; if the log or reviewed supplied graph has changed, run the check again. Rules that cannot be evaluated completely within the analysis budget cannot be exported. Large worklists can take time and temporary local disk space; the temporary file is removed after the response.

Columns ending in `_review` are for spreadsheet review: cells that could be interpreted as formulas are prefixed with an apostrophe. They are not a lossless interchange format. The paginated violation API retains the original identifiers when exact values are needed.
