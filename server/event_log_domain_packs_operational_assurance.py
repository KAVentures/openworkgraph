"""R23 operational-control packs: recorded evidence, not adopted policy.

Uses the existing pack builder, role mapping, installation and review engine.
No external system action or authorization is introduced.
"""
from .event_log_domain_packs_regulated_services import _build

ACCESS_RECERTIFICATION = _build(
    "access_recertification",
    "Periodic access recertification",
    "Review a selected entitlement certification record from review opening through recorded disposition, implementation and verification.",
    "recert",
    "cert_case",
    "Entitlement certification record",
    [
        ["opened", "Open certification review", True],
        ["assigned", "Assign review owner", True],
        ["evidence", "Record entitlement evidence", True],
        ["queried", "Record clarification request", False],
        ["decided", "Record retain or revoke disposition", True],
        ["applied", "Record disposition implementation", True],
        ["verified", "Record implementation verification", True],
        ["closed", "Close certification record", True],
    ],
    [
        ["assign_hours", "Assignment within (elapsed hours of opening)", 24, "opened", "assigned"],
        ["decision_hours", "Decision within (elapsed hours of assignment)", 168, "assigned", "decided"],
        ["apply_hours", "Implementation within (elapsed hours of decision)", 48, "decided", "applied"],
        ["verify_hours", "Verification within (elapsed hours of implementation)", 48, "applied", "verified"],
    ],
    [
        ["query_records", "queried"],
        ["decision_records", "decided"],
        ["verification_records", "verified"],
    ],
    [
        ["opened", "assigned"],
        ["assigned", "evidence"],
        ["evidence", "queried"],
        ["evidence", "decided"],
        ["queried", "decided"],
        ["decided", "applied"],
        ["applied", "verified"],
        ["verified", "closed"],
    ],
    [
        "opened",
        "decided",
        "closed",
    ],
    [
        ["applied", "verified"],
    ],
    [
        "Select one entitlement-review record, not a whole employee or a batch of unrelated permissions. For retain dispositions, map applied only to an explicit recorded implementation or confirmation.",
        "A recorded retain/revoke decision is not authorization, least-privilege proof, effective revocation, or identity verification. No permission is changed.",
        "Actor separation compares supplied resource labels only; service accounts and missing actors need independent review.",
    ],
)

VULNERABILITY_REMEDIATION = _build(
    "vulnerability_remediation",
    "Vulnerability remediation administration",
    "Review selected remediation records from registration through assessment, treatment implementation, retest and recorded closure.",
    "vuln",
    "remediation_case",
    "Vulnerability remediation record",
    [
        ["registered", "Register remediation record", True],
        ["assessed", "Record assessment", True],
        ["assigned", "Assign treatment owner", True],
        ["deferred", "Record deferral review", False],
        ["treated", "Record treatment implementation", True],
        ["retested", "Record retest", True],
        ["reviewed", "Record closure review", True],
        ["closed", "Close remediation record", True],
    ],
    [
        ["assess_hours", "Assessment within (elapsed hours of registration)", 24, "registered", "assessed"],
        ["assign_hours", "Assignment within (elapsed hours of assessment)", 24, "assessed", "assigned"],
        ["treat_hours", "Treatment within (elapsed hours of assignment)", 168, "assigned", "treated"],
        ["retest_hours", "Retest within (elapsed hours of treatment)", 48, "treated", "retested"],
    ],
    [
        ["deferral_records", "deferred"],
        ["treatment_records", "treated"],
        ["retest_records", "retested"],
    ],
    [
        ["registered", "assessed"],
        ["assessed", "assigned"],
        ["assigned", "deferred"],
        ["assigned", "treated"],
        ["deferred", "treated"],
        ["treated", "retested"],
        ["retested", "reviewed"],
        ["reviewed", "closed"],
    ],
    [
        "registered",
        "closed",
    ],
    [
        ["treated", "retested"],
    ],
    [
        "Select one remediation lifecycle per identified issue/asset scope; a multi-asset finding may need multiple cases. Acceptance without treatment and reopened cases require separate scope.",
        "No severity, exploitability, exposure, remediation effectiveness, risk acceptance validity or security assurance is verified. No scanning or system action occurs.",
        "Elapsed targets are locally declared; paused clocks, severity-specific service levels, and coordinated deployment windows are not inferred.",
    ],
)

INVENTORY_DISCREPANCY_RECONCILIATION = _build(
    "inventory_discrepancy_reconciliation",
    "Inventory discrepancy reconciliation",
    "Review recorded stock-count discrepancy cases from detection through recount, reconciliation, approval and recorded adjustment verification.",
    "invrec",
    "discrepancy_case",
    "Inventory discrepancy record",
    [
        ["detected", "Record inventory discrepancy", True],
        ["assigned", "Assign discrepancy owner", True],
        ["recounted", "Record recount", True],
        ["queried", "Record discrepancy query", False],
        ["reconciled", "Record reconciliation proposal", True],
        ["approved", "Record approval", True],
        ["posted", "Record inventory adjustment posting", True],
        ["verified", "Record adjustment verification", True],
        ["closed", "Close discrepancy record", True],
    ],
    [
        ["assign_hours", "Assignment within (elapsed hours of detection)", 8, "detected", "assigned"],
        ["recount_hours", "Recount within (elapsed hours of assignment)", 24, "assigned", "recounted"],
        ["reconcile_hours", "Reconciliation within (elapsed hours of recount)", 48, "recounted", "reconciled"],
        ["post_hours", "Posting within (elapsed hours of approval)", 24, "approved", "posted"],
    ],
    [
        ["query_records", "queried"],
        ["recount_records", "recounted"],
        ["posting_records", "posted"],
    ],
    [
        ["detected", "assigned"],
        ["assigned", "recounted"],
        ["recounted", "queried"],
        ["recounted", "reconciled"],
        ["queried", "reconciled"],
        ["reconciled", "approved"],
        ["approved", "posted"],
        ["posted", "verified"],
        ["verified", "closed"],
    ],
    [
        "detected",
        "closed",
    ],
    [
        ["reconciled", "approved"],
        ["posted", "verified"],
    ],
    [
        "Select discrepancies following an approved adjustment path. No-adjustment cases, rejected proposals, reversals, multiple stock locations and parallel recounts need separate reference paths.",
        "An adjustment activity does not establish physical stock, quantity correctness, valuation, accounting validity or absence of fraud. No inventory or ERP write is made.",
        "The case identifier should represent one discrepancy resolution record, not a SKU shared across unrelated discrepancies.",
    ],
)

CONTINUITY_EXERCISE_REVIEW = _build(
    "continuity_exercise_review",
    "Business continuity exercise review",
    "Review administrative evidence for a selected planned continuity exercise, including readiness, execution record, findings, corrective action and closure review.",
    "bcex",
    "exercise_case",
    "Continuity exercise record",
    [
        ["planned", "Record exercise plan", True],
        ["approved", "Record exercise plan approval", True],
        ["prepared", "Record readiness check", True],
        ["conducted", "Record exercise conducted", True],
        ["findings", "Record findings", True],
        ["reworked", "Record corrective-action clarification", False],
        ["corrected", "Record corrective action completion", True],
        ["verified", "Record corrective action review", True],
        ["closed", "Close exercise record", True],
    ],
    [
        ["approval_hours", "Approval within (elapsed hours of planning)", 168, "planned", "approved"],
        ["readiness_hours", "Readiness within (elapsed hours of approval)", 336, "approved", "prepared"],
        ["findings_hours", "Findings within (elapsed hours of exercise)", 72, "conducted", "findings"],
        ["corrective_hours", "Corrective action within (elapsed hours of findings)", 720, "findings", "corrected"],
    ],
    [
        ["clarification_records", "reworked"],
        ["findings_records", "findings"],
        ["verification_records", "verified"],
    ],
    [
        ["planned", "approved"],
        ["approved", "prepared"],
        ["prepared", "conducted"],
        ["conducted", "findings"],
        ["findings", "reworked"],
        ["findings", "corrected"],
        ["reworked", "corrected"],
        ["corrected", "verified"],
        ["verified", "closed"],
    ],
    [
        "planned",
        "conducted",
        "closed",
    ],
    [
        ["planned", "approved"],
        ["corrected", "verified"],
    ],
    [
        "Select planned exercises with a corrective-action closure milestone. No-action exercises, multiple independent action items and actual disaster recovery operations need different models.",
        "Recorded exercise and review milestones do not prove recoverability, recovery objectives, failover success, resilience or regulatory compliance. No exercise or recovery procedure is executed.",
        "Targets are elapsed administrative review intervals, not recovery time or recovery point objectives.",
    ],
)

OPERATIONAL_ASSURANCE_PACKS = {
    p["pack_id"]: {p["version"]: p} for p in (
        ACCESS_RECERTIFICATION,
        VULNERABILITY_REMEDIATION,
        INVENTORY_DISCREPANCY_RECONCILIATION,
        CONTINUITY_EXERCISE_REVIEW,
    )
}
