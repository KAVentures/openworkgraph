# Session-owned local analysis job console (R8)

Open the existing saved-log analysis builder and expand **Local analysis job
console**. Refresh the inventory to see only jobs belonging to the current
dashboard session, including other logs in that session. The table shows
state, submitted/finished timestamps, exact identifiers, execution-time source
binding availability, byte size and remaining retention. It intentionally
omits errors, query definitions, result content and other sessions' queue
statistics.

Successful background results are normally erased as soon as the existing
builder consumes them. To retain a successful result for later inspection,
explicitly tick **Retain a successful background result in this session until
expiry** before running the analysis. Only a successful result received by
the still-current, uncancelled view is retained; cancelled, stale and failed
attempts use the existing cleanup. Retention uses the existing deadline and
expiry measured from submission, not a new storage system. Navigation does
not extend retention, and process restart erases all jobs.

Download of a retained result requires explicit confirmation. It is the
original immutable historical result, not a fresh-source validation; it can
contain sensitive data. This separate historical download does not change
the existing verified current-source export or sharing flow.

Cancel active jobs or erase terminal jobs individually using existing
session-scoped endpoints. Bulk cleanup requires reviewing and confirming
the exact terminal population digest. If another job becomes terminal, expires,
or is erased, the request refuses rather than removing an unreviewed population.
The operation never cancels active jobs or changes another session's jobs.
No new queue, durable history, source access or query execution is introduced.
