# Purpose limitation and data minimization

> Implementation guidance only; not legal advice.

OpenWorkGraph can produce detailed workplace context. A technically available field or query is not automatically necessary for every purpose. Swedish IMY guidance emphasizes that employee processing should have specific, explicit and legitimate purposes; employers should assess whether individual-level processing is necessary or whether statistics/less identifiable processing is sufficient.

Official guidance: https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/grundlaggande-principer/

## Recommended purpose hierarchy

Prefer the least intrusive mode that can achieve the documented purpose:

1. **Aggregate process patterns** — first choice for organization/process redesign.
2. **Pseudonymous evidence** — only when longitudinal individual traces are necessary but direct identifiers are not.
3. **Team-scoped identifiable evidence** — only for a documented team-level operational need.
4. **Organization-wide identifiable evidence** — exceptional; require explicit authorization, audit and documented necessity.

Do not enable a broader mode merely because it is convenient for analysis.

## Suggested acceptable-purpose examples

These examples still require a case-specific legal assessment:

- identify repeated workflows suitable for automation;
- detect process bottlenecks at aggregate level;
- reconstruct an employee's own recent work context for an approved assistant;
- understand handoffs between tools/systems for process engineering;
- evaluate whether an automation reduces repeated manual steps.

## High-risk / separate-review uses

Require a separate purpose, necessity/proportionality assessment, worker transparency and legal review before enabling uses such as:

- individual productivity scores or rankings;
- disciplinary profiling;
- compensation/promotion decisions;
- covert monitoring;
- continuous real-time manager surveillance;
- health, union, political, religious or other sensitive profiling;
- automated employment decisions with legal or similarly significant effects.

## Configuration worksheet

For each enabled purpose, record:

| Control | Decision | Reason |
| --- | --- | --- |
| Population / teams observed | | |
| Applications excluded | | |
| Browser hosts excluded | | |
| Event types shared to Gateway | | |
| Individual raw access enabled | | |
| Aggregate-only access available | | |
| Minimum aggregate cohort `k` | | |
| Retention period | | |
| External integrations | | |
| Export permissions | | |

## Change control

Treat the following as material changes that should trigger privacy/governance review before rollout:

- adding a new sensor or new data field;
- enabling screenshots, typed content or clipboard contents in a fork/customization;
- expanding from a pilot team to a larger workforce;
- adding organization-wide raw trace access;
- extending retention materially;
- changing from process improvement to employee evaluation;
- adding a new AI/automation recipient;
- combining OpenWorkGraph evidence with HR/performance datasets.

The product's raw-rich-data philosophy means retaining useful work context for AI; it does **not** mean every available context field should be shared to every organizational reader. Capture, sharing and access are separate policy layers.