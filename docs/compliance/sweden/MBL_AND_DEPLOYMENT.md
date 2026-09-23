# MBL and OpenWorkGraph deployment — Swedish implementation note

> General implementation guidance only; not legal advice. Whether a particular rollout triggers a duty to negotiate depends on the employer, collective agreements, workforce and planned change. Involve employment counsel and relevant worker representatives early.

## Why assess MBL before rollout

Under 11 § of the Swedish Co-Determination Act (Medbestämmandelagen, MBL), an employer bound by a collective agreement must, on its own initiative, negotiate with the relevant employee organization before deciding on an important change to the business or an important change to the working/employment conditions of employees covered by the organization.

An OpenWorkGraph rollout **does not automatically mean that 11 § applies in every case**. However, a deployment that materially changes how work is observed, analyzed, managed, automated or assessed can raise co-determination questions. Treat MBL review as an early deployment gate rather than an after-the-fact formality.

Official law: https://www.riksdagen.se/sv/dokument-och-lagar/dokument/svensk-forfattningssamling/lag-1976580-om-medbestammande-i-arbetslivet_sfs-1976-580/

## Questions to resolve before a decision

- Which employees and organizational units are affected?
- Is a collective agreement in force, and which organizations are relevant?
- Does the deployment materially change work methods, management, monitoring, automation or employment conditions?
- Will managers obtain individual-level evidence, or only aggregate process patterns?
- Are data used for performance assessment, discipline, pay, staffing or scheduling?
- What technical/privacy controls are enabled?
- What is the pilot scope, duration and review process?
- What information should be provided to employee representatives before negotiations?

## Suggested deployment process

1. Define the narrow business purpose and prohibited uses.
2. Complete privacy/DPIA assessment before deciding on the rollout.
3. Map access: self, team, aggregate, pseudonymous and organization-wide raw evidence.
4. Prefer aggregate/process-level access where individual evidence is unnecessary.
5. Prepare the employee transparency notice.
6. Determine whether MBL consultation/negotiation duties apply and complete them before the relevant decision where required.
7. Record agreed safeguards, pilot limits and review triggers.
8. Communicate the final scope before collection starts.

## Topics worth documenting with worker representatives

- exact capture fields and explicit exclusions;
- no typed text/clipboard contents/ordinary key identities/screenshot bytes in the standard Gateway contract;
- whether individual-level views exist and who may use them;
- whether productivity scoring/ranking is prohibited;
- retention and deletion;
- employee self-access and complaint/escalation routes;
- auditability of manager/admin access;
- how workflow inferences are treated as non-authoritative;
- change-control process for new sensors, fields, purposes or integrations.

## Separate GDPR obligations

MBL analysis does not replace GDPR analysis. Employee data processing still needs a lawful basis, transparency, purpose limitation, data minimization, appropriate security and retention. IMY guidance also notes that workplace IT monitoring can require a DPIA.

Relevant IMY guidance:
- https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/kontroll-och-overvakning-av-anstallda/
- https://www.imy.se/verksamhet/dataskydd/dataskydd-pa-olika-omraden/arbetsliv/tillaten-behandling--vilka-krav-galler/grundlaggande-principer/
