# Bulk acknowledgement of automation incidents (R18)

In Evidence → Automation monitoring → Incidents, filter the existing
incident list by deployment, status and search. Open **Acknowledge selected
open incidents together**, choose up to 50 visible open records (or explicitly
select the first 50 visible open records), and optionally enter a shared
review note. Prepare an exact review, inspect all listed identities, state
pins, count bases and the digest, then explicitly confirm acknowledgement.

The server checks each selected ID, occurrence count, last-seen timestamp,
review revision and retained run-membership digest against the current
incident store. The preview also pins a hash of each complete incident
record and the normalized note. Application recomputes the same review under
the existing incident lock, validates the whole batch before mutation, and
publishes a single atomic replacement using the existing incident writer.
A change to any selected record refuses the entire batch. Unselected
incidents are unchanged. Replayed or concurrent duplicate acknowledgements
are refused. Existing note retention limits and review revision semantics
are preserved.

The operation only changes open to acknowledged. It does not resolve,
reopen, suppress, delete, execute, retry or contact any provider. Acknowledging
an incident does not independently verify underlying run membership or
business outcomes. Individual evidence inspection and resolution remain
available in the existing interface. No new data store, external service,
authorization grant, or collection is introduced.

Both endpoints require the existing dashboard session, strict bounded JSON,
and exact explicit selection. The apply endpoint requires the exact preview
digest and explicit confirmation. API/MCP bearer tokens are not sufficient.
