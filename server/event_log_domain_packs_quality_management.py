"""R23 quality-management administration packs; recorded milestones only."""
from .event_log_domain_packs_regulated_services import _build

CAPA = _build(
    "corrective_action_management", "Corrective and preventive action management",
    "Inspect recorded issue investigation, action planning, implementation and effectiveness review.",
    "capa", "action_case", "Corrective action record",
    [
        ("opened", "Open action record", True),
        ("investigated", "Record investigation completed", True),
        ("queried", "Record investigation clarification", False),
        ("planned", "Record action plan", True),
        ("approved", "Record plan approval", True),
        ("implemented", "Record action implementation", True),
        ("verified", "Record effectiveness review", True),
        ("closed", "Close action record", True),
    ],
    [("investigate_hours", "Investigation within (elapsed hours of opening)", 168, "opened", "investigated"),
     ("approve_hours", "Approval within (elapsed hours of planning)", 72, "planned", "approved"),
     ("implement_hours", "Implementation within (elapsed hours of approval)", 720, "approved", "implemented"),
     ("verify_hours", "Review within (elapsed hours of implementation)", 720, "implemented", "verified")],
    [("queries", "queried"), ("implementation_records", "implemented"), ("review_records", "verified")],
    [("opened", "investigated"), ("investigated", "queried"), ("investigated", "planned"),
     ("planned", "approved"), ("approved", "implemented"), ("implemented", "verified"), ("verified", "closed")],
    ["opened", "approved", "closed"], [("planned", "approved"), ("implemented", "verified")],
    ["Select action records with one reviewed implementation and closure path. Multiple parallel actions, rejected plans and reopened records need a separately reviewed model.",
     "An investigation or effectiveness-review milestone is not proof of root cause, remediation effectiveness, regulatory compliance or product safety.",
     "No action approval, closure or release authority is granted; resource separation compares recorded labels only."],
)

CALIBRATION = _build(
    "calibration_administration", "Calibration administration",
    "Review scheduling, recorded calibration, certificate review and administrative closure.",
    "calq", "calibration_case", "Calibration administration record",
    [
        ("requested", "Record calibration request", True),
        ("scheduled", "Record calibration schedule", True),
        ("performed", "Record calibration performed", True),
        ("exception", "Record calibration exception", False),
        ("certificate", "Record certificate received", True),
        ("reviewed", "Record certificate review", True),
        ("closed", "Close calibration record", True),
    ],
    [("schedule_hours", "Scheduling within (elapsed hours of request)", 72, "requested", "scheduled"),
     ("perform_hours", "Calibration record within (elapsed hours of scheduling)", 336, "scheduled", "performed"),
     ("certificate_hours", "Certificate within (elapsed hours of calibration)", 72, "performed", "certificate"),
     ("review_hours", "Certificate review within (elapsed hours of receipt)", 48, "certificate", "reviewed")],
    [("exceptions", "exception"), ("certificate_records", "certificate"), ("review_records", "reviewed")],
    [("requested", "scheduled"), ("scheduled", "performed"), ("performed", "exception"),
     ("performed", "certificate"), ("certificate", "reviewed"), ("reviewed", "closed")],
    ["requested", "closed"], [("performed", "reviewed")],
    ["Select one calibration administration record per service cycle, not an entire asset lifetime. Failed calibration and out-of-tolerance disposition may require separate workflows.",
     "No measurement values, tolerances, certificate validity, traceability, equipment safety or fitness for use are evaluated.",
     "Recorded calibration or review does not permit equipment operation or release."],
)

DOCUMENT_CONTROL = _build(
    "controlled_document_revision", "Controlled document revision",
    "Review recorded drafting, review, approval, publication and supersession acknowledgement.",
    "docctl", "revision_case", "Controlled document revision record",
    [
        ("requested", "Open revision request", True),
        ("drafted", "Record draft prepared", True),
        ("returned", "Record revision query", False),
        ("reviewed", "Record document review", True),
        ("approved", "Record revision approval", True),
        ("published", "Record publication", True),
        ("acknowledged", "Record supersession acknowledgement", True),
        ("closed", "Close revision record", True),
    ],
    [("draft_hours", "Draft within (elapsed hours of request)", 168, "requested", "drafted"),
     ("review_hours", "Review within (elapsed hours of draft)", 72, "drafted", "reviewed"),
     ("publish_hours", "Publication within (elapsed hours of approval)", 24, "approved", "published"),
     ("ack_hours", "Acknowledgement within (elapsed hours of publication)", 168, "published", "acknowledged")],
    [("queries", "returned"), ("review_records", "reviewed"), ("publication_records", "published")],
    [("requested", "drafted"), ("drafted", "returned"), ("drafted", "reviewed"),
     ("reviewed", "approved"), ("approved", "published"), ("published", "acknowledged"), ("acknowledged", "closed")],
    ["requested", "approved", "published", "closed"], [("drafted", "approved")],
    ["Select one approved publication revision, not the entire document history. Rejections, emergency releases and multiple audience acknowledgements may need a distinct model.",
     "No document contents, signatures, policy adoption, distribution coverage or effective supersession are verified.",
     "Publication and acknowledgement labels do not establish that a recipient read or follows a document; no publication is performed."],
)

RECALL_NOTIFICATION = _build(
    "product_recall_notification_admin", "Product recall notification administration",
    "Review recorded notification preparation, review, dispatch and reconciliation for a declared campaign.",
    "recall", "notification_case", "Recall notification campaign record",
    [
        ("opened", "Open notification record", True),
        ("scoped", "Record recipient scope prepared", True),
        ("drafted", "Record notice draft", True),
        ("approved", "Record notice approval", True),
        ("dispatched", "Record notice dispatch", True),
        ("exception", "Record delivery exception", False),
        ("reconciled", "Record dispatch reconciliation", True),
        ("closed", "Close notification administration", True),
    ],
    [("scope_hours", "Scope record within (elapsed hours of opening)", 24, "opened", "scoped"),
     ("approve_hours", "Approval within (elapsed hours of draft)", 24, "drafted", "approved"),
     ("dispatch_hours", "Dispatch within (elapsed hours of approval)", 24, "approved", "dispatched"),
     ("reconcile_hours", "Reconciliation within (elapsed hours of dispatch)", 72, "dispatched", "reconciled")],
    [("exceptions", "exception"), ("dispatch_records", "dispatched"), ("reconciliation_records", "reconciled")],
    [("opened", "scoped"), ("scoped", "drafted"), ("drafted", "approved"), ("approved", "dispatched"),
     ("dispatched", "exception"), ("dispatched", "reconciled"), ("reconciled", "closed")],
    ["opened", "approved", "closed"], [("drafted", "approved"), ("dispatched", "reconciled")],
    ["Select one defined notification campaign or independently closed batch. This is administrative review, not a recall initiation, product risk assessment or recipient-level coverage model.",
     "Illustrative elapsed targets are not regulatory reporting deadlines, safety instructions or permission to delay any required action.",
     "Dispatch and reconciliation records do not establish delivery, recall effectiveness, recovered quantities, safety or regulatory compliance; no notices are sent."],
)

QUALITY_MANAGEMENT_PACKS = {
    p["pack_id"]: {p["version"]: p}
    for p in (CAPA, CALIBRATION, DOCUMENT_CONTROL, RECALL_NOTIFICATION)
}
