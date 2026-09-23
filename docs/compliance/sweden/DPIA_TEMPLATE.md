# DPIA template — OpenWorkGraph workplace deployment

> Template only; not legal advice. Complete this for the actual deployment, purposes, users, integrations, retention, workforce and applicable law. Do not copy the suggested answers without validating them.

## 1. Deployment owner

- Organization / legal entity:
- Data controller(s):
- Processor(s) / sub-processors:
- DPO / privacy contact:
- Security owner:
- Product owner:
- Relevant worker representatives / unions consulted:
- Assessment date / reviewers:

## 2. Proposed purposes

State each purpose separately and precisely. Avoid generic purposes such as “analytics” or “improvement.”

| Purpose | Necessary outcome | Individual-level data necessary? | Aggregate alternative considered? | Lawful basis assessment |
| --- | --- | --- | --- | --- |
| Example: identify repetitive cross-application workflows suitable for automation | process redesign | Preferably no | Yes — aggregate/process patterns first | Complete locally |
| | | | | |

Explicitly document prohibited or out-of-scope secondary purposes. A recommended default for OpenWorkGraph deployments is **no individual productivity scoring, ranking, disciplinary profiling, or covert monitoring** unless a separate documented assessment establishes a lawful, necessary and proportionate basis.

## 3. Processing description

Describe the exact data flow:

```text
endpoint sensors
→ local privacy hardening
→ local evidence store
→ optional customer-controlled Gateway
→ authorized REST/MCP/human access
→ approved analysis/automation use
```

Document which modes are enabled:

- Local-only capture:
- Organization Gateway:
- Browser sensor:
- Human OIDC/SSO access:
- Aggregate-only access:
- Pseudonymous individual access:
- Raw individual trace access:
- External AI/automation integrations:

### Data categories expected

OpenWorkGraph may process privacy-hardened workplace context such as timestamps, application/window/page context, event/action type, interaction/effort metadata, safe UI labels, device/session/actor identifiers and copy/paste occurrence/linkage.

The standard Gateway contract excludes typed text, clipboard contents, ordinary key identities and screenshot bytes. Confirm that local configuration and any extensions/custom sensors preserve that boundary.

### Data subjects

- Employees:
- Contractors / consultants:
- Other people whose names or context may incidentally appear in workplace UI/page titles:

## 4. Necessity and proportionality

For each purpose, answer:

1. Why is the processing necessary rather than merely useful?
2. Could the outcome be achieved with aggregate statistics instead of identifiable traces?
3. Could fewer applications, event types, fields, teams or time periods be observed?
4. Is continuous capture necessary, or can sampling / bounded pilot windows achieve the purpose?
5. Are excluded/private applications and browser hosts configured?
6. Are individual-level reads limited to people with a documented need?
7. Is the retention period tied to the stated purpose?
8. What prevents repurposing the evidence for incompatible performance monitoring?

## 5. Lawful basis and special-category data

Record the legal basis relied upon for each purpose and why it applies. Employee consent should not be assumed to be freely given merely because a user interface can display a consent control.

Assess whether incidental context could reveal special-category data or highly private information. Record technical and organizational measures for avoiding, excluding, minimizing or deleting such data.

## 6. Transparency

Before collection begins, document how workers will receive clear information covering at least:

- what OpenWorkGraph captures and deliberately does not capture;
- purposes and legal basis;
- whether individual-level access is possible and under what conditions;
- recipients / categories of recipients;
- retention periods or criteria;
- transfers outside the EU/EEA if any;
- automated decision-making/profiling, if any;
- rights and contact routes;
- how to raise concerns without retaliation;
- changes to purposes or scope before they take effect.

Use the accompanying employee notice as a starting point.

## 7. Access and separation of duties

Document the chosen roles and scopes:

- employee self-access:
- team-level raw trace access:
- organization-wide raw trace access:
- aggregate-only access:
- pseudonymous access:
- machine/service integrations:
- admin access:

Prefer aggregate-only access when individual traces are unnecessary. Keep broad organization-wide raw evidence access exceptional and auditable.

## 8. Retention and deletion

- Gateway retention period:
- Local endpoint retention policy:
- Audit-log retention:
- Backup retention:
- Procedure for actor/time/session-scoped deletion:
- Procedure when exclusion/privacy settings change retroactively:
- Procedure for departing workers:

Remember that deleting live database rows does not automatically erase customer-managed backups; define backup expiry and restore procedures too.

## 9. Risk assessment

Rate likelihood and severity before and after controls.

| Risk | People affected | Initial risk | Controls | Residual risk | Owner |
| --- | --- | --- | --- | --- | --- |
| Excessive monitoring / chilling effect | | | aggregate-first access; purpose limits; transparency | | |
| Function creep into productivity scoring | | | explicit prohibited uses; governance review | | |
| Unauthorized manager access | | | OIDC; team scopes; least privilege; audit | | |
| Re-identification of pseudonymous traces | | | treat as personal data; prefer aggregate access | | |
| Sensitive/private context captured incidentally | | | exclusions; local hardening; short retention; deletion | | |
| Data breach / token theft | | | TLS; secret management; rate limits; revocation; logging | | |
| Employee unable to understand processing | | | pre-collection notice; accessible policy; support route | | |
| Inaccurate inference used as fact | | | raw evidence canonical; inferred tasks non-authoritative | | |
| | | | | | |

## 10. Worker / stakeholder consultation

Record consultation with:

- DPO/privacy counsel:
- information security:
- HR / employment law:
- works council / union representatives where applicable:
- representative employees / pilot participants:

Summarize objections and changes made in response.

## 11. Decision

- Approved / approved with conditions / rejected:
- Approved purposes:
- Prohibited uses:
- Approved teams/population:
- Pilot dates:
- Retention:
- Access model:
- Review date:
- Trigger events requiring a fresh DPIA/review (new purpose, new data fields, broader workforce, new integration, individual scoring, materially longer retention, etc.):

## Official Swedish guidance to consult

- IMY — basic principles for employee data: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/grundlaggande-principer/
- IMY — employee monitoring: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/kontroll-och-overvakning-av-anstallda/
- IMY — DPIA in employment: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/konsekvensbedomning/
