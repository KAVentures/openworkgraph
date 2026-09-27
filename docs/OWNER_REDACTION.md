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

## Contextual redaction for AI context

The Redacted AI-context layer (see [PRIVACY_AND_DATA.md](PRIVACY_AND_DATA.md#three-layers-raw-redacted-safe-allowlist)) runs the presentation pipeline above and then `server/contextual_redaction.py`, which replaces only the sensitive span with a typed stable token:

| Input | Redacted |
|---|---|
| `Re: Contract for Anna Svensson - Gmail` | `Re: Contract for PERSON_xxxxxx - Gmail` |
| `Faktura 4471 från Erik Lindqvist - Outlook` | `Faktura 4471 från PERSON_xxxxxx - Outlook` |
| `Call Dr. Lindqvist about lab results` | `Call Dr. PERSON_xxxxxx about lab results` |
| `Meeting with Johan and Anna re Q3 budget - Google Calendar` | `Meeting with PERSON_xxxxxx and PERSON_yyyyyy re Q3 budget - Google Calendar` |
| `Acme AB - Account - Salesforce` | unchanged |

Detection is local and deterministic, with no network and no model:

- **Deterministic identifiers** (unchanged): emails, phones, personnummer, `Name <email>`, plus 10+ digit reference numbers as `ID_…`.
- **Name lists**: about 4,400 first names and 4,200 surnames from the US Census Bureau 1990 name files (public domain) and Statistics Sweden name statistics, 31 Dec 2022 (CC0). See `server/name_lexicon/README.md`.
- **Context rules (English and Swedish)**:
  - names after `from / for / to / with / by / about / re / cc / chat with / meeting with / call / Dr. / Mr. / Ms. / Hej / från / för / till / med / av / hos / om / möte med / samtal med`;
  - `First Surname` pairs;
  - `First and First` / `First och First` / `First / First`;
  - `Surname, First`;
  - a title segment that is only a first name (`Johan | Microsoft Teams`);
  - possessives.
- **Learned identities** apply everywhere once learned, including identities first seen while serving a response. Those are kept in process memory only; read paths never write the registry file.
- **Never replaced**:
  - app/site names, UI and business vocabulary, month/day names;
  - large organizations whose name is also a surname (`Ericsson`);
  - words before a place/organization noun (`Hope Street`, `Chase Bank`);
  - the user's **never redact** list.
- **Ambiguous names**: names that are also everyday words, places or products (`May`, `Bill`, `Grace`, `Paris`, `Claude` …) need an adjacent listed surname or a strong person cue.
- **Always redact**: the user's list is always replaced.
- **OWNER**: owner handling above is unchanged.

Tokens use the installation's local HMAC key, so the same person maps to the same token in Gmail, Salesforce, Sheets and Teams on this installation. Tokens cannot be reversed off the machine.

### Measured quality

`tests/fixtures/redaction_eval.jsonl` has 246 hand-labelled titles and labels (English and Swedish):
- **Apps covered:** Gmail, Outlook, Teams, Slack, Calendar, Salesforce, HubSpot, Jira, GitHub, Google Docs/Sheets, a clinical journal system and generic UI labels.
- **Hard negatives:** company, product and place names, and names that are also words.

Run `python scripts/redaction_eval.py --verbose`:

| Metric | Result | Required |
|---|---|---|
| Name recall (labelled names fully replaced) | **0.982** (111/113) | ≥ 0.95 |
| Over-redaction (non-sensitive words replaced) | **0.010** (9/929) | ≤ 0.05 |

**Known misses:**
- `Bill Gates` (ambiguous first name, surname not in the list).
- `Florence Nightingale Museum` (skipped by the place rule).

**Known over-redactions:**
- Brands and holidays named after people: `Morgan Stanley`, `Jack Daniels`, `Martin Luther King Jr. Day`.
- Two mail rows whose whole segment the existing mail-row policy replaces (`Thanks, Laura!`, `Utvecklingssamtal Fredrik Lund`).

Add such names to **never redact**.

Performance: about 20 µs per uncached title, cached per string. The default `get_workflow_trace` call measured 129 ms without and 132 ms with AI redaction (600 events).

## Architectural boundary

Presentation redaction must not be used to mutate task timing or event structure. Event IDs, actions, timestamps, durations, counts and task boundaries are preserved.

For the separate rules governing credentials, Swedish personal identifiers, payment cards, OCR/reference numbers, IBANs and other sensitive values at persistence time, see [Privacy and data handling](PRIVACY_AND_DATA.md).
