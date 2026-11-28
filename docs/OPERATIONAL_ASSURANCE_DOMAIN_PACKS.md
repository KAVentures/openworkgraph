# Operational assurance domain packs (R23)

Four independently versioned process models extend the existing saved-log
catalog. Each has seven case metrics, four configurable elapsed-hour targets,
recorded-event sequence checks, duplicate milestone controls, separation-of-
duties review, an optional exception stage and an explicit reference lifecycle.

| Pack | Select one case per | Reference scope |
| --- | --- | --- |
| Periodic access recertification | entitlement certification record | Opened → assigned → evidence → disposition → implementation → verification → closed |
| Vulnerability remediation administration | remediation record for a defined issue/asset scope | Registered → assessed → assigned → treatment → retest → review → closed |
| Inventory discrepancy reconciliation | discrepancy resolution record | Detected → assigned → recounted → reconciled → approved → posted → verified → closed |
| Business continuity exercise review | planned exercise record | Planned → approved → prepared → conducted → findings → corrective action → verified → closed |

Use Evidence → saved event logs → domain packs. Import authorized, selected
CSV/XES/OCEL evidence, choose the pack and map each required role to a literal
source label. Select only records following the described path; legitimate
alternate paths must not be interpreted as policy breaches. Review the
configurable elapsed-hour defaults before installing. Unmapped optional
activities omit affected metrics and rules with explicit not-applicable
entries. The existing report contains exact case metrics, reviewable rule
witnesses and reference-model conformance. Reconfigure to revise the definition
digest; uninstall removes only the pack's owned metrics.

The templates do not verify permission effectiveness, security assurance,
actual inventory, valuation, accounting validity, recoverability, identity,
legal compliance or business outcomes. Resource separation is descriptive
of recorded actors, not proof of independent human approval. No provider
contact, access change, scanning, ERP write, or recovery action is introduced.
The continuity-exercise pack's clocks are administrative review intervals,
not recovery time or recovery point objectives.

Validation includes independent synthetic CSV lifecycles with known late,
missing, duplicate, inverted, same-actor and unknown-resource cases, parameter
revisions, optional omissions, atomic rejected installations, global metric
name uniqueness and independent metric preservation on uninstall. Real
customer mappings and full integration CI are still needed.
