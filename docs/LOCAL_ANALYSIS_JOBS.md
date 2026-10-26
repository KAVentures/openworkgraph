# Cancellable local analyses

The local dashboard's **Build an analysis** runs as a cancellable local job by
default. **Cancel analysis** withholds the result immediately in that view and
requests cancellation of the server job. Queued, running, complete, cancelled,
timed-out and failed states are distinct. Failed/cancelled/timed-out jobs never
return partial rows. Download and save become available only for a complete
result. Each new run clears the previous result and sharing preview.

This is local, single-process query execution over an already imported log.
Nothing is uploaded and Observe, AI context, connector permissions and business
execution authority do not change. The typed query language and existing metric,
variant, row and memory limitations are unchanged. Uncheck **Run as a cancellable
local job** to use the compatible synchronous route; that legacy route has no
job cancellation/deadline. Simulation and other analysis tools retain their
existing execution behavior.

Jobs use the log **when execution starts**, under the existing workspace lock,
which excludes concurrent append/delete during the query. Submission does not
pin a dataset revision or reserve a historical snapshot. An append before
execution is included. The returned log metadata describes that execution;
results are static and do not claim current source freshness or a content digest.
Rerun after changing the log. Saved analysis definitions and sharing exports
still use their existing explicit actions and may recompute against later data.

## Limits and lifecycle

- One worker, at most four unfinished jobs globally, twenty retained jobs globally.
  Admission returns429 instead of pruning history or launching unbounded threads.
- Two-minute deadline includes queue and workspace-lock wait. The worker checks
  cancellation between statements/fetches; a timer interrupts only its owned
  DuckDB connection. Cancellation can release the workspace while other work is
  waiting. It does not interrupt a different query or forcibly kill a thread.
  Native calls still need to return from interruption; this is not a hard OS
  process-kill guarantee.
- Complete serialized results are limited to2MiB; oversized output fails visibly.
  The existing engine's query-memory and bounded variant handling still apply.
- Job definitions, status and results stay only in process memory and expire ten
  minutes after submission, including when no client polls. No credentials are
  retained by the job store; ownership uses a SHA-256 dashboard-session key.
- Every API call requires a currently valid dashboard session and the exact
  submitting session. Another session on the same computer cannot inspect,
  cancel, delete or download a known job ID. AI bearer tokens cannot access jobs.
- Removing a job cancels unfinished work and erases its retained result. Deleting
  a log cancels and erases all its jobs before waiting for the workspace lock.
  Late workers cannot restore erased or cancelled results.
- The dashboard removes each job after retrieving its result or leaving its run.
  Leaving a view stops polling; disconnected/closed pages may leave work until
  the deadline and memory until expiry. Cleanup requests can fail on network or
  session changes; the UI does not claim they succeeded. A new session cannot
  recover the old session's jobs. Reload/restart does not resume jobs or results.

Cancellation after a job has already completed is an idempotent no-op; deletion
can still erase it. A cancellation request during an in-flight submission or
result read suppresses that response in the view and requests cleanup. Failed
runs cannot keep an earlier result exportable. Log/case-notion/view changes
suppress late responses. No percentage completion is invented.

## API

All routes require `Authorization: OWG-Session <current session>` and return
`Cache-Control: no-store` on successful responses. Body parsing uses the existing
64KiB local JSON request bound. Typed validation runs before queue admission;
unknown log IDs fail as a job when the worker checks the workspace.

| Method | Route suffix after `/v1/event-logs/{log_id}` | Meaning |
| --- | --- | --- |
| POST | `/analysis-jobs` | Submit the existing analysis spec;202 plus job ID/status |
| GET | `/analysis-jobs/{job_id}` | Status, timestamps, spec digest and limits; no query/rows |
| GET | `/analysis-jobs/{job_id}/result` | Exact existing analysis result only after success;409 otherwise |
| POST | `/analysis-jobs/{job_id}/cancel` | Idempotent cancellation; terminal state stays terminal |
| POST | `/analysis-jobs/{job_id}/delete` | Cancel and erase; future reads404 |

Job IDs are random and are not credentials. A different session/log or expired,
erased or unknown job all return404. Status errors omit driver SQL, imported
values and stack traces. The spec digest covers the canonical validated query;
it is not a source fingerprint or independent verification receipt.

This closes a bounded local R8 execution path. Durable/shared multi-user job
scheduling, large tenant datasets, concurrent database writes/reads, crash
recovery and company-scale performance acceptance remain open in issue370.

The reproducible owned oracle is
`python scripts/benchmark_local_analysis_jobs.py --cases 100000` in the source
venv. It creates its own temporary database with one million events, alternating
A/B, ten events per case and one-minute spacing. Independent expected totals are
100,000 cases,54,000,000 elapsed seconds,540-second p90 and500,000 A events.
It checks the actual asynchronous engine result, reports platform/versions,
wall time and available peak-process RSS, and erases its jobs/database. It uses
SQL-seeded fixtures; it does not establish import throughput or customer-scale
performance. The existing separate scale workflows remain required.
