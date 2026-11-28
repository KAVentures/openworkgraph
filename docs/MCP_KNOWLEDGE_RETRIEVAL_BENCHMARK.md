# Offline reviewed knowledge retrieval benchmark

Run from the repository root:

```sh
python -m scripts.benchmark_knowledge_retrieval --records 10000 --match-every 17
python -m scripts.benchmark_knowledge_retrieval --records 10000 --match-every 1
python -m scripts.benchmark_knowledge_retrieval --records 10000 --match-every 1 --adaptive
python -m scripts.benchmark_knowledge_retrieval --records 10000 --match-every 0
python -m pytest -q tests/test_benchmark_knowledge_retrieval.py
```

The benchmark uses a fixed synthetic AI-visible catalog with predictable
literal match membership. It exercises the real discovery and pagination
helpers and the task knowledge companion reader. The baseline is a hypothetical
full-record delivery of all pages, not an assertion that the old MCP tool could
retrieve a whole catalog. The first three matching references are retrieved
as a predetermined benchmark choice, not as a semantic ranking.

Measure exact coverage and duplicates, source page calls and scanned record
occurrences, local read canonical JSON bytes (including repeated reads),
shortlist plus selected companion bytes before MCP annotations, and elapsed
local helper time. These bytes are not model tokens or network bytes;
local timing includes fixture lookup, encoding and filtering, not database,
middleware, MCP transport or model execution. The benchmark does not establish
real-world model quality, realized savings or semantic relevance.

## Example measured run

On the isolated development runtime, 10,000 synthetic records with every
17th record matching returned 589 references with zero duplicates, using
100 discovery reads plus three exact reads. The full-page baseline was
14,190,689 JSON bytes; all shortlist results plus the selected companion were
215,066 bytes. The local read volume was still 14,195,468 bytes. This is a
model-facing payload reduction, not a local scan volume reduction.

When every record matched, scan_limit=100 and result_limit=20 caused
49,800 scanned record occurrences because continuation restarts after the
last returned match. Following the optional next_scan_limit_hint reduced this
to 10,080, with identical coverage and 500 discovery calls in both cases.
Total local read bytes including exact companion reads fell from 70,674,820
to 14,391,129. Local helper time in this single illustrative run was
0.649 s vs 0.162 s; do not generalize this timing to deployment.

The existing pagination response now offers next_scan_limit_hint. When the
shortlist is truncated, it suggests min(scan_limit, result_limit); otherwise
it retains the current scan size. It is null at the end. Clients may explicitly
pass it on the next request along with the same query and next_after. Nothing
changes the caller's requested scan size automatically. A smaller hint can
increase calls if match density falls later, so clients can choose a larger
page again. No additional read or new search engine is introduced.

These results are for static synthetic records, not an atomic live catalog.
Use full integration CI and a live, authorized connector test for actual
transport, database and redaction performance. Evaluate external AI quality
separately with the existing paired context experiment.
