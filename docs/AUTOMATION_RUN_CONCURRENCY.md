# Retained run concurrency review (R18)

Open automation monitoring for a selected deployment and choose **Review retained
run concurrency**. Select a completed UTC date window (exclusive end, at most
366 days), an exact reported specification hash or explicitly unknown
attribution, and a declared positive concurrent-run threshold.

The review reads the complete bounded population of up to 10,000 retained
records with start before the end and finish at/after the start, or no
finish. Positive terminal intervals are clipped to the selected window;
carry-in and carry-out are counted. Missing terminal timestamps, zero-duration
and boundary-only records are excluded and separately counted. An older
unfinished record may overlap the window, but no duration is imputed.

An exact microsecond sweep over half-open intervals produces peak measured
concurrency, a duration histogram, occupied time, time at/above the threshold,
and aggregate excess run-microseconds. At the same timestamp, endings and
starts do not overlap. Sorted start/end arrays count positive pair overlap
without enumerating the potentially quadratic number of pairs. For example,
10,000 simultaneous intervals have 49,995,000 unordered overlapping pairs.

Select evidence for constant-concurrency segments, measured runs ranked by
overlapping peers, or excluded records. Each page contains the full review
summary and histogram. The page pin covers exact retained interval records,
deployment/design attribution and review settings, not step evidence.
Aggregate run-microseconds are exported as decimal strings to preserve
integers above the JavaScript safe-integer range. Editing inputs clears the
result; paging stale evidence refuses. Export is local.

This is retrospective analysis of currently retained reported timestamps, not
live concurrency, historical reporting state, true resource consumption,
queue delay, worker utilization, service loss or actual capacity. A zero
measured count does not prove idle capacity. No model or runtime is contacted,
no business state is changed, and no staffing decision is authorized.
