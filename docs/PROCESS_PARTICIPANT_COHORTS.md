# Participant and team cohorts

Authorized event-log imports can include participant and team labels alongside
case identifiers, activities and timestamps. The existing explorer shows label
support, adjacent label changes and complete-case cohorts. Canonical desktop
observations and company evidence permissions are unchanged.

In the local dashboard's event-log importer:

1. Choose an authorized file and inspect its columns on this device.
2. Map the case, activity and timestamp columns. Optionally select participant
   and/or team columns; these are excluded by default, including when a header
   already uses those names.
3. Confirm the selected columns and local analysis authorization. Applying the
   mapping replaces identifiers in participant/team columns with
   `participant_1`, `participant_2`, `team_1`, etc. before any upload.
4. Optionally show the local lookup to identify these labels from the selected
   file. This lookup stays in browser memory, can be hidden, and clears when
   mapping changes or the page reloads. It is not uploaded, retained in OWG,
   included in process designs or sent to AI agents.
5. Analyze the mapped log. Preview is ephemeral. A separate explicit retention
   action is required to save its minimized local snapshot.

The lookup identifies source labels, not independently verified people or team
membership. Labels can represent people, shared accounts, roles or automated
actors. Masking selected columns does not establish anonymization: other chosen
columns follow the existing CSV rules. Review them before analysis.

The report contains import-scoped aliases, event counts, distinct-case support,
missing-label counts and adjacent label-change signals. Counts cover all supplied
cases rather than just displayed examples. Missing labels do not establish changes. Changes touching tied-time event
groups have ambiguous adjacency and are counted separately.
Positive inter-event gaps also do not establish human handoffs, employee effort,
business outcomes or causal dependencies.

After optional retention, use the participant/team dropdowns under **Filter
complete retained cases**. With both filters selected, a matching event must
contain both labels. Every event of each selected case remains included,
including other participants' work. The map, variants, timings and drill-down
use this complete-case population. Dataset case ordinals, variant IDs and
original import aliases remain stable; filtering does not rename participants
or create edges by clipping other people's steps.

Retained snapshots store only aliases, with the existing local expiry and
deletion controls. They do not retain the native identifiers from the selected
participant/team columns. Reopened snapshots retain alias choices and event
histories; the browser-only identity lookup is not recovered. Aliases are scoped
to their original import and are never used to join identities across snapshots.
Existing snapshot drift comparisons continue to compare paths and timings,
without inferring participant identity across imports.

The local API requires a valid dashboard session plus
`X-OWG-Participant-Labels-Authorized: true` to preview or retain these columns.
Ordinary AI bearer tokens cannot authorize this metadata path. Retention also
requires the existing independent retention confirmation. Direct API inputs may
contain only the bounded alias format, never native identifiers. Case filters
use `contains_participant` and `contains_team`; both operate inside one selected
dataset. Requests and responses remain bounded and no-store.

Older structural snapshots gain nullable event-label columns without changing
their events or acquiring participant claims. The original four-field structural
parser remains available and never implicitly opts into participant metadata.
Process designs, agent projects, observations, synchronization and AI-history
access receive no new identity data or grants.

Verification covers browser minimization and lookup isolation, exact received
preview/retention bytes, alias validation, missing/tied labels, complete-case
intersection, support beyond sample caps, stable identities, schema upgrade,
reload, mobile layout and deletion. Tests use owned synthetic files, isolated
SQLite databases and the production local API; they do not certify customer
identity, consent or source completeness.
