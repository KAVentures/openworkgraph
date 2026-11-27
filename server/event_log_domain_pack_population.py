"""Coverage of the untransformed event-linked case projection.

A retained object with no event does not become a nonactivated/satisfied
case. The standard compliance engine projects event-linked objects only.
Keep the excluded population visible, and fail closed on denominator drift.
"""
from . import event_log_workspace as workspace


def counts(conn, log_id, case_type):
    retained, linked = conn.execute("""
        SELECT count(*), count(*) FILTER (WHERE EXISTS (
            SELECT 1 FROM event_objects eo JOIN events e
              ON e.log_id=eo.log_id AND e.event_id=eo.event_id
            WHERE eo.log_id=o.log_id AND eo.object_id=o.object_id
        ))
        FROM objects o WHERE o.log_id=? AND o.object_type=?
    """, [log_id, case_type]).fetchone()
    return {"case_type":case_type, "retained_objects":retained,
            "projected_cases":linked, "objects_without_linked_events":retained-linked,
            "all_retained_objects_projected":retained==linked,
            "source_completeness_verified":False}


def require_count(population, actual):
    if actual != population["projected_cases"]:
        raise workspace.EventLogWorkspaceError(
            "Evaluated case population does not match the reviewed projection")
