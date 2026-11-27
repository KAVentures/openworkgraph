# Object-centric operational reference packs (R23)

These three versioned packs add operational process templates to the existing
Event Log Workspace → Domain Packs catalog. They use the existing OCEL 2.0
import, mapping, retained event log, metric library, compliance and object-model
engines. No additional service, connector, credentials or background processing
is introduced.

| Pack ID | Parent object | Child object | Default check / closure target |
| --- | --- | --- | --- |
| inbound_shipment_receiving | Shipment | Handling unit | 8 / 24 elapsed hours |
| production_batch_sample_review | Production batch | Sample record | 24 / 72 elapsed hours |
| field_service_job_tasks | Field service job | Job task | 48 / 96 elapsed hours |

## Setup and review

1. Import an authorized object-centric log containing the two distinct object
   types and their explicit event links. A flat case CSV cannot establish these
   two populations; use OCEL rather than generating synthetic child identities.
2. Select the pack in the workspace. Map the parent and child types and the
   opening, check and closure activities to exact retained labels. Optionally
   map the child exception activity.
3. Review the reference path and set elapsed-hour targets. These defaults are
   illustrative, not contractual, safety, clinical or regulatory thresholds.
4. Install the pack and run its report. The installation records the version,
   mapping, parameters and resolved-definition digest. Review the separate
   parent and child metric coverage, rule examples, cardinality results and
   model deviations. Optional unmapped exception definitions are disclosed.
5. Reconfigure or uninstall using the existing pack controls. Uninstallation
   removes only pack-owned definitions, not source events.

## Source shape

For the supported reference path, an opening event links one parent and all
selected children. Each check event links an individual child. The closure
event links the same parent and all selected children. The optional exception
is an individual recorded child milestone. Real operations may be more complex:
partial closes, transfers, retries, split receipts and reopened records need
a separately reviewed mapping and must not silently be interpreted as failures.

Example (illustrative IDs, not customer data):

| Event | Time | Linked objects |
| --- | --- | --- |
| Open | 08:00 | shipment-1, unit-A, unit-B |
| Check | 09:00 | unit-A |
| Check | 09:15 | unit-B |
| Close | 10:00 | shipment-1, unit-A, unit-B |

The engine checks parent and child populations separately, one parent at opening
and closing, one child per check, required child links at shared milestones,
and each child's unique parent across opening, closing and all retained shared
events. The latter exposes a cross-parent swap even when the per-event counts
look normal. Synchronization checks every child related through a retained shared
event, using recorded time/event-ID order. It does not infer a time-effective
relationship or prove actual physical membership. The earliest recorded check
can satisfy the prerequisite; a recorded check is not a passed inspection.

Missing events may indicate a partial log, an ongoing case or an actual
deviation. Reference-model fitness is not source completeness or verified
business compliance. Do not combine parent and child denominators into a
business case count. No pack authorizes shipment acceptance, product release,
physical work or customer commitments.

## Acceptance

The regression fixture imports a real synthetic OCEL log through the retained
workspace, with a normal two-child lifecycle, missing check, late check/closure,
unclosed parent and swapped child closure. It exercises installed definitions,
per-population deadline denominators, synchronization, parent cardinality,
parameter reconfiguration, optional omission, required mapping refusal and
uninstallation. These fixtures are synthetic and do not establish source-system
or customer acceptance. A representative authorized source export and operator
setup review are still needed before claiming parity for these verticals.
