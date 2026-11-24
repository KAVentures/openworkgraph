# Local discovery source snapshots

After discovering a process in the imported-log workspace, **Record source
snapshot and review draft** records the source context before opening the
unsaved catalog draft. The action explicitly retains only digests, the selected
case notion and filters, discovery settings, coverage counts and algorithm
bounds in the existing imported-log database. It copies no graphs or cases.
Business goals and rules still require the normal separate review and save.

The server repeats discovery with the displayed settings and cohort. If the
generated draft differs from the preview, recording is refused and the analyst
must discover again. The source snapshot describes data at recording time;
it is not a claim that the earlier preview saw an identical dataset. Source
hashes cover every event, object (including type and attributes) and event-object
link, independently of row order. Structured JSON row hashes escape delimiters.
The source hash algorithm is `openworkgraph.discovery-source.v1`.

Each snapshot has a content-addressed opaque reference:
`urn:openworkgraph:local-log-discovery:<sha256>`. The catalog carries it in the
existing reviewed-knowledge `evidence_refs` field. No shared process-model or
runtime specification field is added. Repeating an identical snapshot reuses
the reference and first recording time. Each log holds at most 100 distinct
snapshots; reaching the limit refuses new records and never silently prunes
references. Existing identical records can still be reused.

**Inspect discovery source snapshots** checks a saved catalog revision. It
shows the exact historical selection and whether source data is unchanged,
changed, unavailable or could not be checked. Altered snapshot metadata that no
longer matches its content-addressed reference is refused and labelled separately.
Source changes do not rewrite a
reviewed model. Saving a goal, rules or other edits changes the model digest;
the reference is then explicitly user-selected and its derivation is not
independently verified. Even an exact generated-model digest match establishes
only matching model bytes. Supplied logs do not independently establish business
truth, approved rules, causal relationships, business outcomes or execution
authority. Unsaved editor changes are excluded from this saved-revision check.
Successful local fingerprint comparisons report their check time. The panel is
an on-demand check; inspect it again after changes rather than treating it as
continuous monitoring.
The reviewed design may still be exported as a disabled starter independently
of raw-log retention; doing so cannot restore missing evidence or verify it.
Only snapshot metadata is versioned here. Older dataset contents are not copied
or reconstructed after the source advances; the historical digest and selection
must not be presented as replayable historical cases.

The automation pipeline evidence journey resolves the pinned process revision's
references too, with minimized source/selection digests, counts and status.
It omits the actual filter values. A source reference remains visibly
unavailable after deletion; importing an identically named log cannot retarget
it. Local references may travel in an explicit knowledge export, but the source
selection and dataset do not travel with them and cannot be resolved remotely
without separately authorized access.

Deleting the imported log deletes its snapshot metadata in the same log store.
Separately reviewed process knowledge remains until the user forgets it; its
opaque source reference may no longer resolve. An in-flight recording may
finish after the user changes views, but cannot open a stale catalog draft.
The later deletion removes its metadata too. The hashes are local provenance,
not signatures or independent attestations.

Fingerprinting is limited to 30,000,000 source rows (events + objects + links).
Recording a larger source is refused without a partial snapshot. One source
inspection checks at most three distinct logs and 30,000,000 rows in total,
caching repeated log checks within the request. Other references remain visible
with a **not checked: work budget exceeded** status; historical metadata is never
presented as a current source check. These are local-workspace limits, not
enterprise-scale acceptance or a claim of historical data completeness.

An owned synthetic-data acceptance run with DuckDB 1.5.6 used 1,000,000 events
and 100,000 cases: recording took 2.96 s and lookup 2.16 s. At 10,000,000 events,
1,000,000 cases and 21,000,000 source rows, recording took 33.78 s and lookup
37.91 s; peak whole-test process RSS was 2,745 MiB. This is full-source
fingerprinting, which can take longer than ordinary analysis queries, and is
reported as pending in the UI. Timings are observations on this execution
environment, not a latency guarantee or customer-data acceptance.

Reproduce with `OWG_SCALE_ACCEPTANCE=1 python -m pytest -q -s
tests/test_process_discovery_provenance.py`; set
`OWG_DISCOVERY_SCALE_EVENTS=10000000` for the larger source. CI runs the 1M-event
fingerprint acceptance alongside the existing 10M-event workspace acceptance.

## Local API

- `GET /v1/event-logs/{id}/model-discovery` remains read-only and additionally
  returns `process_model_sha256` and bounded discovery-method metadata.
- `POST /v1/event-logs/{id}/model-discovery-draft` requires a dashboard session
  and `X-OWG-Local-Storage-Confirmed:true`. The strict JSON body contains
  `expected_model_sha256`, optional `case_type`, `noise`, `activities` and
  `cohort`. It accepts no client-supplied model or source metadata. Discovery
  and snapshot recording run under the existing workspace lock in a worker.
- `GET /v1/event-log-discovery/process-origin?workflow_id=…&revision=…` inspects
  the saved local revision and its source selections. It requires a dashboard
  session; AI bearer tokens cannot read imported-log details.

Responses use `Cache-Control:no-store`. None of these operations reads a live
business-system provider, enables observation, grants permissions or runs an
automation. Legacy catalog records without snapshot references remain unlinked.
The journey's existing `source_read_performed:false` field is scoped explicitly
to live providers (`source_read_scope:live_provider_only`); authorized retained
log fingerprint checks are local and described by the per-reference status.

## Effective discovery input and exact cohort inspection (R5)

The raw dataset fingerprint alone cannot detect changed case membership caused
by analyst tags or changed labels caused by active activity mapping/hiding.
The dashboard discovery request now opts into an effective input fingerprint:
the full case-event projection **after** hidden-activity filtering, activity
mapping/collapse and the selected cohort filter. It hashes structured case
key, event key, effective activity, timestamp, resource and event attributes,
with case and event counts. The original discovery source reference pins both
the raw dataset and the effective projection. The dashboard sends the preview
model digest and projection digest when recording; either changing refuses
the save. No implicit approval or new graph revision is granted.

The catalog and automation opportunity journey distinguish source advancement,
effective projection changes and unperformed checks. Older snapshots remain
resolvable with effective input explicitly not recorded. The fingerprint checks
have independent bounded work budgets; they never report a partial projection
as current. The effective input fingerprint has a 30M case-event-row limit;
source inspection also limits underlying source rows to 30M. Origin checks
process at most six distinct effective projections with a 30M underlying-row
budget in addition to existing raw fingerprint limits.

**Inspect exact discovery cohort** reopens the selection pinned to the
saved process revision, not the current event-log UI filter. It reads current
data using the original case notion, filters and settings. Original and
current projection counts/digests are shown separately. If they differ,
the result is labelled current rather than a historical case reconstruction.
The local read-only inspector pages exact complete cases, then selected
case events, rejecting a case outside the selected cohort. Pagination pins
the freshly checked effective-input digest; a change stops the review until
it is reopened. Event ordering at equal timestamps is a stable event-ID tie
break, not evidence of the real order.

For selections of at most 1,000 cases and 20,000 projected events the
recorded snapshot additionally retains a complete index of log-scoped
case-key digests and projected event-row digests. No raw case keys, paths,
or rows are copied in this index. This is local pseudonymous fingerprinting,
not anonymization. Inspection can compare complete bounded populations and
report added, removed, modified and unchanged case projections, filtering
the live rows by status. Removed raw case keys cannot be reconstructed.
Larger or legacy selections return no partial case-change breakdown. Case
fingerprints are not included in source metadata or portable handoff packets.

The exact inspection endpoint is POST /v1/event-log-discovery/inspect-selection
with workflow_id and revision query parameters, a content-addressed source
reference and expected process digest in the strict JSON body. Subsequent
case/event pages require the current effective projection digest. Requests
are authenticated local dashboard only and responses are no-store; no
external data retrieval or modifications occur. The ordinary log workspace
filters are unchanged. Original observed execution logs also retain the
existing explicit original-observation recheck from this inspector.

Portable project handoff reviews carry only the minimized original/current
effective input digests and check status, not filter values, case IDs or
the case index. Changed, unavailable or unrecorded effective inputs remain
human-review blockers, not automated permission decisions.

### Rediscover a saved selection and review a successor revision

From a saved process revision's exact source reference, **Rediscover current
data for this selection** runs the existing inductive discovery algorithm
with the original case notion, cohort and settings. It previews the fresh
model, original/current model and projection digests, coverage, and differences
from the saved reviewed graph. It records no new source metadata or design.

**Record refreshed source and prepare successor draft** requires a separate
explicit retention confirmation and both fresh preview digests. Changes to
the projected cases or generated graph invalidate the request. The original
source reference must still resolve and belong to the same saved revision.
The dashboard checks the latest process revision before and after retention;
an intervening process edit leaves any recorded metadata separate and opens
no successor draft. The proposed successor retains the new exact source
reference, but no inherited business goal, reviewed rules, tool mappings,
decision tables, manual gates, outcome expectations or approvals. It stays
unsaved until the existing optimistic revision-save flow receives a new human
review. Prior designs and their artifacts are unchanged. The unsaved successor
does not offer the saved-revision project export or pipeline actions until
saved. No customer action or source-system write occurs.

POST /v1/event-log-discovery/rediscover takes the exact workflow_id and revision
query parameters and a strict body with reference and expected_process_sha256.
With retain=true, the local dashboard must also send the confirmed local
storage header and expected_model_sha256 and expected_projection_sha256.
Both modes use the original selection server-side; no client-supplied filter
or replacement graph is accepted. Preview and recording have the same bounded
source and projection work budgets.
