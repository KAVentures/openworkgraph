# Event-log workspace on real public logs

Synthetic fixtures prove semantics; they do not show how the workspace behaves on the size and
messiness of real logs. This page records a reproducible sweep of the workspace over two public
process-mining logs, what failed or was slow, and what was changed as a result. It is evidence for
the competitor-style review and scale items in #326 and #370 (R8), not a latency guarantee.

## How to reproduce

Download a log yourself (nothing is downloaded by the script), then run:

```
python scripts/real_log_acceptance.py BPI_Challenge_2012.xes.gz --format xes > bpi2012.json
python scripts/real_log_acceptance.py my-export.csv --format csv \
    --case-column CaseID --activity-column Activity --time-column Timestamp > mine.json
```

The script imports the log into a temporary workspace (deleted afterwards) and calls every
`GET /v1/event-logs/{log_id}/...` route without parameters, and every POST analysis route that does
not change data with an empty body and with a one-activity case filter (the most frequent activity).
It prints each call's status and seconds, every server error or exception, and every call slower
than `--slow` seconds (default 20). A 4xx answer means the route needs parameters the sweep does not
invent; it is not a failure. The exit code is 1 when any call answered with a server error.

## Logs

| Log | Source | Events | Cases | Notes |
| --- | --- | --- | --- | --- |
| BPI Challenge 2012 | 4TU.ResearchData, doi:10.4121/uuid:3926db30-f712-4394-aebc-75976070e91f | 262,200 | 13,087 | Loan applications; lifecycle transitions |
| BPI Challenge 2017 | 4TU.ResearchData, doi:10.4121/uuid:5f3067df-f10b-45da-b98b-86ae4c7a310b | 1,202,267 | 31,509 | Loan applications; 26 activities |

## Results (2026-11-25)

Machine: 4 vCPUs (Intel Xeon 2.1 GHz), 15 GB RAM, Python 3.13, DuckDB 1.5.6, `main` at the time.

| | BPI 2012 | BPI 2017 |
| --- | --- | --- |
| Import (XES.gz) | 11.5 s, 0 rejected | 59.5 s, 0 rejected |
| Calls made | 296 | 296 |
| Answered 200 | 116 | 115 |
| Server errors or exceptions | **0** | **0** |
| Median / 90th percentile of answered calls | 0.3 s / 2.4 s | 0.9 s / 9.2 s |
| Calls over 20 s | 0 | 5 |

Slowest answered calls on BPI 2017: OCEL SQLite export 84.7 s, trace clusters 54.6 s (with and
without a filter), HTML report 35.7 s, model change 28.2 s, story 16.1 s, quality report 15.5 s.
Everything else answered in under 15 s; most analyses in under 2 s.

## Independent cross-check with PM4Py

The discovered Petri nets for BPI Challenge 2012 (exported as PNML) were replayed in OWG and, on
the same log exported as XES, with PM4Py 2.7 token-based replay and ETConformance precision:

| Net (noise) | Fitness OWG / PM4Py | Fitting cases OWG / PM4Py | Precision OWG / PM4Py |
| --- | --- | --- | --- |
| 0.2 | 1.0 / 1.0 | 100% / 100% | 0.0839 / 0.0821 |
| 0.5 | 0.9998 / 0.9998 | 99.3% / 99.2% | 0.087 / 0.0859 |

OWG replays the 2,000 most frequent variants (10,721 of 13,087 cases, disclosed as
`cases_excluded`); PM4Py replays every case, which explains the small precision and share
differences. Both nets are permissive on this log (precision below 0.1): BPI 2012 interleaves offer,
application and work-item events, which the discovery draws as an any-order region.

## What was found and changed

| Finding | Cause | Change |
| --- | --- | --- |
| Trace clusters took ~50 s on BPI 2017 | Binding a Python list in a query (`unnest(?)`) made DuckDB try `import pandas` once per value; without pandas each attempt searched every import path (1.6 million searches) | #1396: a missing pandas is recorded once; 49.7 s → 8.1 s, same result |
| OCEL SQLite export took 89 s on BPI 2017 | The whole log was written as OCEL JSON text and parsed again | #1393: the document is built as data; 89 s → 53 s, identical output |
| Report and model change spent most time in discovery | A fit check replayed every variant after its answer was settled | #1399: stop once settled; report 36.7 s → 32.0 s, model change 28.7 s → 23.0 s, identical models |
| JSON Lines, CSV-with-attributes, Parquet, Excel and trim exports on BPI 2012 | — | Round trips were exact (#1372–#1376, #1362) |

The remaining slow calls are synchronous requests the dashboard waits for. Moving them to the
background job runner, as other long analyses already are, is the next step for logs of this size.
