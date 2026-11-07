# Customer onboarding and activation domain pack

The **Customer onboarding and activation** pack extends the existing **Domain packs**
section in OWG's local saved event-log workspace. Select a saved log, choose this
pack, map the required case and activity roles to **actual values from that log**,
review assumptions and targets, and install. OWG resolves the template against
the selected log, records its version and definitions digest, installs its four
case metrics, and makes its report available through the existing workspace.
Reconfiguration, upgrade refusal, deletion and authorization use existing
domain-pack behavior. This is a reusable process template, **not a new source
connector** or verified source-system view.

## Roles and review workflow

The case type must identify one **onboarding case** in the selected log.
Required activities are **case opened**, **access provisioned**, and
**activation confirmed**. Optional roles are **handoff received**,
**requirements verified**, **rework requested**, and **case closed**.

The report describes:
- elapsed open-to-provision, provision-to-activation and open-to-activation
  durations, plus a count of recorded rework events when mapped;
- recorded ordering of opening/provisioning, verification/provisioning and
  activation/closure, where optional roles make their checks *not applicable*
  when not mapped;
- responses to provisioning within 48 elapsed hours and activation within
  14 elapsed days (editable review targets; neither default is a contractual
  promise);
- more than one activation event within the same onboarding case for review,
  **not** a count of distinct customers or a claim of fraud/error;
- a simple successful-path reference model with optional handoff,
  verification, and closure. Rework and other exceptional paths may be
  flagged as deviations.

Do not map unrelated customer identities to a single case merely because
an ID string matches. Source namespace, completeness and current business
state must be independently verified. Cases that were cancelled, are still
open, are missing events, have multiple products or are partly observed can
appear as missing responses or model deviations. Repeated activation events
can also reflect legitimate retries and corrections. Metrics count observed
event-log activity and **elapsed time including waiting/nonworking hours**,
not employee effort or measured customer value. Missing responses are **not**
independent proof of a missed obligation or breach. Repeated initiation events
are checked independently by the existing response-rule engine.

The synthetic regression fixture in
`tests/test_event_log_domain_pack_customer_onboarding.py` covers a
normal onboarding case, delayed provisioning and activation, early closure,
duplicate activation, recorded rework and an incomplete case. Tests exercise
catalog discovery, install, report, parameter override, optional mappings,
reconfigure, uninstall and refusal on invalid mappings.

No new permission to record, share, provision accounts, contact customers,
execute automations, or read connected identity/CRM systems is conferred.
Case identifiers and activity names can be sensitive operational context:
use the existing user-approved capture/import, retention and sharing controls.
The pack is **not source-system validated**, nor a substitute for a pilot
against an authorized real onboarding process.
