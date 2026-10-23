# Compare retained logs with saved process graphs

The large-log workspace can compare selected cases with an exact revision from
the existing local process catalog. Load saved processes, choose a revision,
and inspect its graph before comparing. Loading a reference reads existing
reviewed knowledge; it does not save a second model, modify the graph, share
local logs, or authorize an agent to execute it.

The comparison pins the process ID, revision and content digest. An older
revision remains usable after a new one is saved. A missing revision is not
replaced by the latest one, and a deleted or recreated revision cannot silently
masquerade as the selected artifact. A changed selection invalidates pending
browser results.

Select a graph activity to browse retained cases containing its exact literal
label. This is label support, not proof that a particular observed event belongs
to that graph node. Activities absent from the displayed log labels do not
select every case. Cohort and exact-path inspection retain complete histories.

## Structural results

The existing graph alignment engine compares observed activity paths with the
saved graph. Exclusive routes and explicit paired parallel regions retain their
existing semantics. Parallel interleavings may match only when all required
branches finish before the join. Conditions and business rules are not evaluated
against missing business facts.

Results partition the full selected case population into structural matches,
structural deviations, and unknown cases. The report also gives analysis
coverage, timestamp-order uncertainty and possible incomplete log prefixes.
An incomplete prefix can differ structurally without establishing a process or
policy violation. Equal timestamps are ordered by event ID for display; that
order is not verified business order. Literal activity labels are never trimmed
or normalized to manufacture a match.

Analysis is bounded:

- Histories over 300 events are excluded whole and reported unknown. They are
  never clipped into a matching prefix.
- Up to 200 variants are selected by frequency by default; the API permits up
  to 1,000. Cases in other variants remain unknown in the full denominator.
- A shared work allowance bounds alignment. Serial/exclusive graphs reserve a
  state-space upper bound before search; parallel graphs consume the existing
  token engine's bounded search states. Exhaustion establishes no verdict.
- At most 20 variant results are displayed. Each retains its complete bounded
  path so **Inspect cases** selects the exact path, not its shortened label.

The match rate among analyzed cases is separate from the lower bound across
all selected cases. Neither is business-policy compliance, source completeness,
employee performance or verified business outcomes. JSON/CSV downloads retain
the exact reference and case denominators.

## Local dashboard API

`POST /v1/event-logs/{log_id}/process-conformance?case_type=order`

```json
{"workflow_id":"review","revision":1,
 "content_sha256":"<64-character digest returned by the process catalog>",
 "cohort":{"resource":"bot"},"max_variants":200}
```

The local dashboard session and JSON content type are required. AI bearer
access is refused. The request body is bounded to 64 KiB. The process reference
must exist in the local catalog and match its digest. Expensive analysis runs
off the event loop; the reference is checked again before returning the result.
Responses are `Cache-Control: no-store` and comparisons are not retained.

Process revisions and retained logs keep their existing storage and deletion
controls. Organization references require their own authenticated review scope
and are outside this local comparison. No AI, source, recording or execution
permission changes.
