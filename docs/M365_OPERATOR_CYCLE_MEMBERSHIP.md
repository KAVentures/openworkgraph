# Microsoft 365 operator-cycle event-membership gate

The operator-facing `python -m server.m365_outlook_operator_cycle extract`
command now accepts `--require-event-membership`. This flag is opt-in;
existing invocations remain unchanged.

Provide the normal explicit local-read, removal-review, delta-read and
local-export consents, prior candidate/CSV, authenticated OWG session,
confirmed log identity, grant, receipt, private journal/handoff directories
and next-round output paths. Add `--require-event-membership` to require
fresh authenticated source-key **and case mapping** membership verification
of the previous candidate before the next Microsoft Graph GET.

A missing, ambiguous, unauthorized or oversized membership readback refuses
the extraction before any provider request. The operator must reconcile the
saved event log manually; the command does **not** automatically replay
uncertain appends, advance a provider checkpoint, or authorize a business
write. Success only means the next bounded extraction artifacts are ready
for human review. Source-event membership is not a guarantee of payload
equivalence, provider completeness or a real Microsoft 365 tenant acceptance
test.

The lower-level `server.m365_outlook_verified_next_round` entrypoint
continues to support the same switch. Never enable this flag by silently
bypassing consent or assuming an OWG session is available.
