# Operations-control domain suite (R23)

Three installable versioned domain packs use the existing **Domain packs** workflow in the saved-log dashboard. They work with a selected case-based CSV (map the case role to `case`) or a selected OCEL object type. Roles are mapped to actual source labels. No data is sent to a service and no source-system connector is required for an imported log.

| Pack | Required milestones | Optional evidence | Metrics and checks |
|---|---|---|---|
| Contract lifecycle management | request, draft, signature, close | legal review, commercial approval, repository filing | 6 metrics, 12 rules; request/draft/signature deadlines, sequence, duplicate signature records and recorded separation of duties |
| Security incident response operations | detection, triage, containment, recovery, close | investigation, post-incident review | 6 metrics, 12 rules; detection-to-triage/containment/recovery, review timing, closure and recorded independent review |
| Maintenance work order operations | request, assignment, start, completion, close | work authorization, inspection | 6 metrics, 13 rules; assignment/start/completion/inspection timing, order, duplicate completion records and recorded independent inspection |

Each includes a version-pinned reference model for conformance checks and declared adjustable elapsed-time parameters. Optional definitions that cannot be resolved are disclosed through the existing not-applicable mechanism. Existing install digests are unaffected: definitions are registered as new pack IDs, and no prior definition is edited. Reconfiguration replaces only the pack's own metrics.

These are observed operational milestones, not legal or security determinations, actual equipment safety checks, or employee performance assessments. A recorded signature does not verify legal authority, containment does not prove remediation, and inspection does not authorize return to service. Deadlines are review assumptions, not real company policy. A reference model is intentionally a simple path; parallel or repeated steps may be valid. Missing or delayed records can reflect incomplete data or ongoing work.

Synthetic tests import independent case fixtures with deliberately late milestones, repeated events, wrong ordering, same-resource review and missing-resource review, and check the actual report, optional roles, parameter reconfiguration, uninstall and invalid mappings. Real ERP/ITSM/contract/security platform acceptance remains outstanding. No source-system validation or compliance certification is claimed.
