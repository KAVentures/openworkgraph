# Owner and person presentation redaction

OpenWorkGraph uses a presentation-time person/owner pseudonymization layer in addition to the separate storage-time sensitive-identifier hardening described in [PRIVACY_AND_DATA.md](PRIVACY_AND_DATA.md).

These are intentionally different mechanisms:

- **Storage-time hardening** removes or pseudonymizes high-confidence secrets and identifiers before persistence.
- **Presentation-time redaction** reduces person-identifying information when local data is returned through dashboard/API/MCP/export surfaces.

## OWNER

The person running the local observer can be represented as `OWNER`.

OpenWorkGraph automatically tries the local OS account display name and username. Optional explicit aliases can be added to `config.json` with `owner_aliases`, plus `owner_emails` and `owner_phones` when desired.

## Other people

High-confidence people can be represented by stable `PERSON_xxxxxx` tokens. A first name that cannot safely be linked to one unique learned identity can be represented generically as `PERSON` instead.

For example, a composite accessibility label such as:

```text
Select Anna Svensson, Contract renewal
```

can be presented as:

```text
Select PERSON_xxxxxx, Contract renewal
```

The useful action/subject context remains while the learned person alias is hidden.

## Persistent identity learning

The local learner is intentionally conservative because a false learned identity can corrupt later analysis.

Strong evidence can include:

- `Name <email>` structures
- sender / recipient / `from` / `cc` / `bcc`
- `reply to`, `email to`, `message from`
- `meeting with`, `call with`, `assigned to`
- other explicitly person-shaped fields such as owner/contact/participant/attendee/assignee

Generic bare `to`/`with` language is not trusted for persistent learning.

Status/team/process vocabulary is rejected from learned identities. Examples include phrases containing words such as `team`, `group`, `department`, `progress`, `done`, `backlog`, `board`, `queue`, `sprint`, `legal`, `finance`, `support` and `review`.

This prevents phrases such as `Transition to In Progress` or `Meeting with Legal Team` from becoming permanent fake people.

## Local alias registry

The learned-person registry is stored locally in `.presentation_people.json`. It stores keyed hashes of aliases and pseudonym tokens rather than literal learned names.

The dashboard exposes **Reset learned person aliases**. Resetting removes only this local registry. It does **not**:

- delete captured event history
- change event IDs, counts, timestamps or durations
- delete normalized/context layers
- remove exported files that already exist

After a reset, future strong person evidence can teach aliases again.

## Architectural boundary

Presentation redaction must not be used to mutate task timing or event structure. Event IDs, actions, timestamps, durations, counts and task boundaries are preserved.

For the separate rules governing credentials, Swedish personal identifiers, payment cards, OCR/reference numbers, IBANs and other sensitive values at persistence time, see [Privacy and data handling](PRIVACY_AND_DATA.md).
