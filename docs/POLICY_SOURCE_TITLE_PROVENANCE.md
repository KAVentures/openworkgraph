# Document-title provenance in policy source revision review (R11)

Open a saved process design in the local dashboard. In the clarification panel,
provide the current documents, expand **Compare previous and current policy
source bundles**, enter the previous exact bundle and run the existing
comparison. Expand **Document-title provenance** to inspect indexed occurrence
counts for each exact title label. Changed title distributions are also shown
on individual statement evidence rows. Download the existing comparison-page
JSON to retain these counts and evidence alongside the original review pin.

For each exact indexed statement text, let a(t) and b(t) be the occurrence
counts in previous and current documents labeled t. The maximum possible
number of exact-text matches is min(sum(a), sum(b)). At most
sum(min(a(t), b(t))) of those matches can have the same title label; that
bound is attainable by matching identical wording within each label first.
Afterwards, the remaining exact matches necessarily cross labels. Their
number is the minimum possible cross-title count among maximum-cardinality
exact-text matchings. The report sums these independent counts over texts.

Identical wording added in another document while its original copy remains
can change the title distribution without requiring a cross-title match.
Repeated occurrences are counted, not deduplicated. Identical titles are
grouped, even when they belong to different supplied documents. A cross-title
match is not evidence that a specific statement moved, that a new document
supersedes an old one, or that its governing authority changed. The bounds
are separate from the existing deterministic stable-order occurrence pairing.

The report covers the existing indexed statement population only. If the
bounded source scan is incomplete, the provenance summary is explicitly
incomplete too. Existing subject flags, pagination, raw citations, human
decisions and saved process design are unchanged. No source text is newly
retained, no provider is contacted, and no authority or execution is granted.

The independent algorithm tests exhaustively enumerate small occurrence
matchings for 500 randomized pairs, plus duplicate-title, copy-growth and
1,500-occurrence cases. The existing source revision and browser suites
include integration regressions for the new metadata and evidence display.

```sh
python -m pytest -q tests/test_process_source_provenance.py tests/test_process_source_revision.py
```
