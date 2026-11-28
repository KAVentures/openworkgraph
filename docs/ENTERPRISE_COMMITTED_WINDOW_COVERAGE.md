# Enterprise committed ingestion windows and backlog

Refs #1572, #326 and #370. The connector checkpoint says where the runtime intends to resume; it is not evidence that every earlier interval has an independently recorded successful request or that the source returned every business event.

## Durable coverage

On successful retained ingestion, the exact admitted request interval is copied from the run journal into a compact interval union, in the same transaction as the retained events, checkpoint and terminal receipt. Failed, refused, rolled-back or invalidated attempts cannot extend the union. Empty successful windows are included. An older in-flight attempt without a recorded admission cannot invent coverage. Existing checkpoints are not retroactively converted into intervals.

Overlapping and adjacent intervals are merged using exact UTC microsecond arithmetic. At most 128 disjoint intervals are retained per profile; if this bound is exceeded the oldest disjoint interval is discarded with an explicit counter. Coverage persists independently of the bounded detailed attempt history. Local profile or dataset deletion removes the associated coverage index. This index describes request windows, not the persistence of every resulting source event, event-occurrence time, source completeness or data authenticity.

## Operator review

Open Enterprise connectors and choose **Committed ingestion coverage and backlog**. Load profiles, select up to 20 and enter a half-open UTC interval of at most 366 days ending no later than the current clock. Review the exact disjoint partition:

- Committed request window: backed by a successful local retention transaction.
- Before configured anchor: not within the original configured advancing interval and not otherwise supported by a recorded request.
- Unknown prior coverage: before or at the current checkpoint, but without an available recorded request interval.
- Not yet checkpointed: at or after the configured anchor/checkpoint, without recorded coverage.

The summary denominator is profile-time, not unique wall-clock time or a weighted business-event population. Inconsistent coverage indexes are excluded with explicit counts. A zero denominator yields an unknown fraction. The reviewed JSON export preserves exact microseconds, whereas displayed hours are rounded. Export is a snapshot of the displayed review, not a fresh provider query.

Backlog estimates use the current clock, checkpoint or initial anchor, configured window length and available minute-based lag. They are illustrative counts of advancing windows, not exact provider cutoffs, a time-to-completion forecast or proof of dispatch eligibility. SAP completed-day and Google application-specific cutoff rules can differ. The estimate excludes rate limits, retries, source density, active holds and scheduling authorizations; these remain visible in other operator controls.

The workbench does not run providers, renew credentials, schedule jobs or repair historical gaps. Historical recovery and tuning require their separate reviewed workflows. A successful request can still omit upstream events or miss records outside its overlap; source deletions and external edits are not reconciled by this index.

## Local API

GET /v1/enterprise-connectors/coverage lists current profile IDs, revisions and anchor/checkpoint values.

POST /v1/enterprise-connectors/coverage/review accepts targets (profile_id and exact revision), since and until. Both require the existing human dashboard session. Responses use owg_enterprise_committed_windows_v1. Review is passive apart from initialization of normal local workspace tables.

## Verification

Regression fixtures cover retained event and empty-window commits, overlap union, pruned journal history, pre-upgrade unknown coverage, injected rollback, missing legacy admission, multi-profile denominators, deletion, malformed indexes, stale revisions and randomized exact interval partition against a discrete oracle. Focused enterprise CI includes the regression module. Live operational acceptance remains separate.
