"""Grant and evidence-bound HTTP scoring regression tests (R1/R2)."""
from contextlib import contextmanager

import pytest
from fastapi import HTTPException

from server import history_routes


@contextmanager
def _connection():
    yield object()


def _setup(monkeypatch, *, grant=None, generation=7):
    from shared import history_policy
    state = {"grant": grant if grant is not None else {"mode": "all_saved"}, "generation": generation}
    monkeypatch.setattr(history_routes, "active_ai_history_access", lambda: dict(state["grant"]))
    monkeypatch.setattr(history_policy, "history_generation", lambda: state["generation"])
    monkeypatch.setattr(history_routes, "connect", _connection)
    return state


def _request():
    return history_routes.ReconstructionReviewSubmission(
        evidence_sha256="a" * 64, annotations=[]
    )


def test_live_scoring_route_uses_host_grant_not_request_lease(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        assert lease == {"mode": "all_saved"}
        assert envelope == {"evidence_sha256": "a" * 64, "annotations": []}
        assert authorize_review() is True
        return {"precision": None, "recall": None, "f1": None, "held_out": False}

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    response = history_routes.score_reconstruction_review_submission(_request())
    assert response.headers["cache-control"] == "no-store"
    assert b'"held_out": false' in response.body


def test_live_scoring_route_denies_missing_grant_before_database(monkeypatch):
    _setup(monkeypatch, grant={"mode": "off"})
    monkeypatch.setattr(history_routes, "connect", lambda: pytest.fail("database read before grant"))
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(_request())
    assert error.value.status_code == 403


def test_live_scoring_route_denies_revocation_during_serialization(monkeypatch):
    state = _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        assert authorize_review()
        state["generation"] += 1
        return {"rows": []}

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(_request())
    assert error.value.status_code == 403


def test_live_scoring_route_rejects_stale_evidence(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        raise ValueError("annotation evidence digest mismatch")

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(_request())
    assert error.value.status_code == 422


def test_dashboard_review_workbench_uses_live_authenticated_routes_without_persistence():
    """Operator entrypoint must stay tied to live evidence and explicit labels."""
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "reconstructionWorkbench" in source
    assert "reconstructionChoices.some(c=>!c.done.checked)" in source
    assert "'/v1/history/reconstruction-review'" in source
    assert "'/v1/history/reconstruction-review/score'" in source
    assert "evidence_sha256:reconstructionEvidence.evidence_sha256" in source
    assert "true_resource_pairs:" in source
    assert "clearReconstructionEvidence('Review refused:" in source
    assert "candidate_pair_precision:result.precision" in source
    assert "candidate_pair_recall:result.recall" in source
    assert "candidate_pair_f1:result.f1" in source
    assert "complete_case_recall_measured:result.complete_case_recall_measured" in source
    assert "missed_source_events_measured:result.missed_source_events_measured" in source
    assert "unseen events cannot be labeled here" in source
    assert "held_out_verified:result.held_out_verified" in source
    assert "cross_case_contamination_measured:result.cross_case_contamination_measured" in source
    assert "localStorage.setItem('reconstruction" not in source
    assert "sessionStorage.setItem('reconstruction" not in source


def test_dashboard_invalidates_in_memory_review_after_grant_or_history_mutation():
    """A successful consent change or session deletion must discard old labels.

    Both mutations occur in History, outside the reconstruction workbench.
    This guards against displaying or scoring stale evidence after the user
    revokes access or deletes retained source events.
    """
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    for route, message in (
        ("/v1/history/ai-access", "History access changed. Reload authorized evidence."),
        ("/v1/history/delete-session", "Saved history changed. Reload authorized evidence."),
    ):
        start = source.index("await call('" + route + "'")
        invalidation = source.index("clearReconstructionEvidence('" + message + "')", start)
        assert invalidation > start
        # Clear immediately after the successful request, before toast/refresh.
        assert "});clearReconstructionEvidence(" in source[start:invalidation + len("clearReconstructionEvidence(")]
        assert "await refresh()" not in source[start:invalidation]



def test_dashboard_rejects_scores_from_superseded_reviewer_labels():
    """A response for old checkbox selections must not be displayed as current."""
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "let reconstructionLabelEpoch=0;" in source
    assert "reconstructionLabelEpoch++;" in source
    assert "const labelEpoch=reconstructionLabelEpoch;" in source
    assert "if(labelEpoch!==reconstructionLabelEpoch){" in source
    assert source.index("if(labelEpoch!==reconstructionLabelEpoch){") < source.index(
        "metrics.textContent=JSON.stringify({"
    )
    assert "Labels changed while scoring. Submit the current review again." in source
    assert "root.querySelector('#reconstructionMetrics').textContent='';" in source


def test_live_review_read_denies_missing_grant_before_database(monkeypatch):
    """Read endpoint must reject absent consent before any event access."""
    _setup(monkeypatch, grant={"mode": "off"})
    monkeypatch.setattr(history_routes, "connect", lambda: pytest.fail("database read before grant"))
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_live_review_read_rechecks_revocation_after_build(monkeypatch):
    """Revoking the lease while a queue is built must suppress its entire payload."""
    state = _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    def candidates(connection, *, lease):
        assert lease == {"mode": "all_saved"}
        state["generation"] += 1
        return {"coverage": {}, "identity_timelines": [], "notice": None}

    monkeypatch.setattr(work_resumption, "resumption_candidates", candidates)
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {"event_reviews": []})
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 403


def test_live_review_read_is_uncacheable_and_non_authoritative(monkeypatch):
    """A valid read is explicitly provisional and must never be cached."""
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "coverage": {"complete": True}, "identity_timelines": [], "notice": None
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "b" * 64
    })
    response = history_routes.get_reconstruction_review()
    assert response.headers["cache-control"] == "no-store"
    import json
    payload = json.loads(response.body)
    assert payload["derived"] is True
    assert payload["authoritative"] is False
    assert payload["review"]["evidence_sha256"] == "b" * 64


def test_reconstruction_submission_rejects_malformed_evidence_digest():
    """Reject noncanonical digests before any scoring or history access."""
    from pydantic import ValidationError

    for digest in ("", "a" * 63, "A" * 64, "g" * 64, "a" * 65):
        with pytest.raises(ValidationError):
            history_routes.ReconstructionReviewSubmission(
                evidence_sha256=digest, annotations=[]
            )


def test_reconstruction_submission_rejects_unbounded_annotation_batch():
    """A malicious client cannot send unlimited review rows into history scoring."""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        history_routes.ReconstructionReviewSubmission(
            evidence_sha256="a" * 64, annotations=[{}] * 193
        )
    assert len(history_routes.ReconstructionReviewSubmission(
        evidence_sha256="a" * 64, annotations=[{}] * 192
    ).annotations) == 192


def test_dashboard_refuses_unscorable_review_batch():
    """Operator must not invite a submission the scoring engine cannot accept."""
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "||reconstructionChoices.length>192" in source
    assert "More than 192 surfaced events; scoring is unavailable" in source


def test_review_http_responses_are_uncacheable_even_on_denial_or_validation():
    """Middleware must cover errors that never reach a route's success return."""
    from server.main import ROOT
    source = (ROOT / "server" / "history_routes.py").read_text(encoding="utf-8")
    assert 'request.url.path.startswith("/v1/history/reconstruction-review")' in source
    assert 'response.headers["Cache-Control"] = "no-store"' in source
    assert source.index('async def reconstruction_review_no_store') < source.index(
        '@app.get("/v1/history/reconstruction-review")'
    )


def test_dashboard_can_label_missing_true_pairs_for_event_level_recall():
    """Candidate-only checkboxes make false negatives impossible by construction.

    The reviewer must be able to add independently observed true resource
    pairs that reconstruction missed, without persisting raw annotations.
    """
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "const missed=document.createElement('textarea');" in source
    assert "missed.setAttribute('aria-label'" in source
    assert "reconstructionChoices[index]={row,pairs,selected,done,missed};" in source
    assert "const extra=c.missed.value.trim()?JSON.parse(c.missed.value):[];" in source
    assert "true_resource_pairs:chosen.concat(extra)" in source
    assert "Invalid additional ground truth:" in source
    assert "reconstructionLabelEpoch++;" in source
    assert "localStorage.setItem('reconstruction" not in source


def test_dashboard_invalidates_review_after_both_retention_policy_paths():
    """Retention updates may delete source events without changing AI grants.

    Both the first-run quick choice and the settings form must discard
    in-memory reviewer labels immediately after the successful policy write.
    """
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    invalidation = "clearReconstructionEvidence('History retention changed. Reload authorized evidence.');"
    assert source.count(invalidation) == 2
    for start_marker, end_marker in (
        ("async function quickPolicy(mode,days){", "function renderPolicy(){"),
        ("host.querySelector('#saveHistoryPolicy').onclick", "function renderAccess(){"),
    ):
        start = source.index(start_marker)
        end = source.index(end_marker, start)
        section = source[start:end]
        assert "await call('/v1/history-policy',{method:'PUT'" in section
        assert section.index("await call('/v1/history-policy'") < section.index(invalidation)
        assert section.index(invalidation) < section.index("await refresh()")


def test_dashboard_exposes_live_provenance_without_persisting_it():
    """Authorized reviewers need scan boundaries and interruption timelines.

    Display as inert text, never HTML, and remove it on revocation/reload.
    This does not assert that any resource pair is a confirmed case.
    """
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "provenance.id='reconstructionProvenance'" in source
    assert "identity_timelines:Array.isArray(data.identity_timelines)" in source
    assert "coverage:data.coverage||{}" in source
    assert "authoritative:data.authoritative" in source
    assert "evidence.textContent=JSON.stringify(" in source
    assert "root.querySelector('#reconstructionProvenance')?.remove()" in source
    assert "localStorage.setItem('reconstruction" not in source


def test_live_review_exposes_direct_pair_provenance_without_case_confirmation(monkeypatch):
    """Operator sees observed event support, not an inferred transitive case."""
    import json
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    direct = [{
        "assignment_status": "observed_same_event_pair",
        "proves_same_case": False,
        "current_evidence_event_ids": ["event-1"],
        "resource_pointers": [
            {"provider": "github", "resource_kind": "issue", "resource_ref": "A"},
            {"provider": "github", "resource_kind": "issue", "resource_ref": "B"},
        ],
    }]
    continuity = [{"observed_only": True, "interruption": "context-switch"}]
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "coverage": {"scan_truncated": False}, "identity_timelines": [],
        "observed_pair_event_assignments": direct,
        "observed_group_continuity": continuity,
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "c" * 64,
        "no_case_identity_inferred": True,
    })
    response = history_routes.get_reconstruction_review()
    payload = json.loads(response.body)
    assert payload["observed_pair_event_assignments"] == direct
    assert payload["observed_group_continuity"] == continuity
    assert payload["authoritative"] is False
    assert payload["review"]["no_case_identity_inferred"] is True
    assert response.headers["cache-control"] == "no-store"


def test_reviewer_provenance_panel_shows_direct_support_as_inert_text():
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "observed_pair_event_assignments:Array.isArray(data.observed_pair_event_assignments)" in source
    assert "observed_group_continuity:Array.isArray(data.observed_group_continuity)" in source
    assert "evidence.textContent=JSON.stringify({" in source
    assert "root.querySelector('#reconstructionProvenance')?.remove();" in source


def test_review_exposes_provisional_case_groups_without_confirmation(monkeypatch):
    """Navigation components must be inspectable but remain non-authoritative."""
    import json
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    groups = [{"resource_pointers": [{"provider": "github", "resource_kind": "issue", "resource_ref": "A"}], "proves_same_case": False}]
    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "coverage": {"scan_truncated": False},
        "observed_case_groups": groups,
        "observed_case_groups_truncated": True,
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "d" * 64,
        "no_case_identity_inferred": True,
    })
    response = history_routes.get_reconstruction_review()
    payload = json.loads(response.body)
    assert payload["observed_case_groups"] == groups
    assert payload["observed_case_groups_truncated"] is True
    assert payload["authoritative"] is False
    assert payload["review"]["no_case_identity_inferred"] is True
    assert response.headers["cache-control"] == "no-store"


def test_review_workbench_shows_provisional_groups_as_inert_evidence():
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "observed_case_groups:Array.isArray(data.observed_case_groups)" in source
    assert "observed_case_groups_truncated:data.observed_case_groups_truncated===true" in source
    assert "evidence.textContent=JSON.stringify({" in source
    assert "root.querySelector('#reconstructionProvenance')?.remove()" in source


def test_reviewer_disagreement_navigation_uses_exact_event_and_window():
    """Disagreement links must not use transitive resource similarity as identity."""
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "choice.row.window===item.window&&choice.row.event_id===item.event_id" in source
    assert "disagreements.slice(0,192).forEach(item=>" in source
    assert "target.scrollIntoView({block:'center'})" in source
    assert "button.textContent='Inspect '+String(item.window)+' · '+String(item.event_id)" in source
    assert "navigation.setAttribute('aria-label','Review event-level disagreements')" in source
    assert "metrics.appendChild(navigation)" in source


def test_save_route_validates_live_evidence_before_persisting(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption
    called = []

    def score(connection, envelope, *, lease, authorize_review):
        assert authorize_review()
        called.append("score")
        return {"f1": 0.0}

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    monkeypatch.setattr(history_routes, "save_reconstruction_review",
                        lambda grant, generation, envelope: (
                            called.append("save") or {"status": "saved_locally"}))
    response = history_routes.save_reconstruction_review_submission(_request())
    assert response.headers["cache-control"] == "no-store"
    assert called == ["score", "save"]


def test_save_route_denies_revocation_before_write(monkeypatch):
    state = _setup(monkeypatch)
    from server import work_resumption

    def score(connection, envelope, *, lease, authorize_review):
        state["generation"] += 1
        return {"f1": None}

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", score)
    monkeypatch.setattr(history_routes, "save_reconstruction_review",
                        lambda *args: pytest.fail("write after revocation"))
    with pytest.raises(HTTPException) as error:
        history_routes.save_reconstruction_review_submission(_request())
    assert error.value.status_code == 403


def test_save_route_revocation_after_write_purges_snapshot(monkeypatch):
    state = _setup(monkeypatch)
    from server import work_resumption
    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review",
                        lambda *args, **kwargs: {"f1": None})
    called = []
    def save(grant, generation, envelope):
        called.append("save")
        state["generation"] += 1
        return {"status": "saved_locally"}
    monkeypatch.setattr(history_routes, "save_reconstruction_review", save)
    monkeypatch.setattr(history_routes, "clear_saved_reconstruction_review",
                        lambda: called.append("purge"))
    with pytest.raises(HTTPException) as error:
        history_routes.save_reconstruction_review_submission(_request())
    assert error.value.status_code == 403
    assert called == ["save", "purge"]


def test_saved_read_denies_without_grant_before_history_query(monkeypatch):
    _setup(monkeypatch, grant={"mode": "off"})
    monkeypatch.setattr(history_routes, "connect", lambda: pytest.fail("history query"))
    with pytest.raises(HTTPException) as error:
        history_routes.get_saved_reconstruction_review()
    assert error.value.status_code == 403


def test_saved_review_read_suppresses_labels_if_history_changes_during_queue(monkeypatch):
    """Stale reviewer labels must not escape after a concurrent history mutation."""
    state = _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    def candidates(connection, *, lease):
        assert lease == {"mode": "all_saved"}
        state["generation"] += 1
        return {"coverage": {"scan_truncated": False}}

    monkeypatch.setattr(work_resumption, "resumption_candidates", candidates)
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda result: {"evidence_sha256": "a" * 64, "event_reviews": []})
    monkeypatch.setattr(history_routes, "read_saved_reconstruction_review",
                        lambda *args: {"annotations": [{"event_id": "private"}]})
    with pytest.raises(HTTPException) as error:
        history_routes.get_saved_reconstruction_review()
    assert error.value.status_code == 403


def test_saved_review_read_rejects_grant_change_during_queue(monkeypatch):
    """A changed grant scope cannot authorize an old review snapshot."""
    state = _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    def candidates(connection, *, lease):
        state["grant"] = {"mode": "off"}
        return {"coverage": {}}

    monkeypatch.setattr(work_resumption, "resumption_candidates", candidates)
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda result: {"evidence_sha256": "a" * 64, "event_reviews": []})
    monkeypatch.setattr(history_routes, "read_saved_reconstruction_review",
                        lambda *args: {"annotations": [{"event_id": "private"}]})
    with pytest.raises(HTTPException) as error:
        history_routes.get_saved_reconstruction_review()
    assert error.value.status_code == 403


def test_review_provenance_reports_http_cap_truncation(monkeypatch):
    """Never silently claim complete provenance when the HTTP projection clips it."""
    import json
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "observed_pair_event_assignments": [{"event_id": str(i)} for i in range(25)],
        "observed_group_continuity": [{"event_id": str(i)} for i in range(25)],
        "observed_case_groups": [{"group_id": str(i)} for i in range(25)],
        "observed_case_groups_truncated": False,
        "observed_pair_event_assignments_truncated": False,
        "observed_group_continuity_truncated": False,
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "a" * 64,
    })
    payload = json.loads(history_routes.get_reconstruction_review().body)
    for field in ("observed_pair_event_assignments", "observed_group_continuity", "observed_case_groups"):
        assert len(payload[field]) == 24
        assert payload[field + "_truncated"] is True
    assert payload["authoritative"] is False


def test_identity_timeline_projection_reports_incomplete_evidence(monkeypatch):
    """A clipped interruption timeline must not look like complete provenance."""
    import json
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "identity_timelines": [{"event_id": str(i)} for i in range(13)],
        "identity_timelines_truncated": False,
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "a" * 64,
    })
    payload = json.loads(history_routes.get_reconstruction_review().body)
    assert len(payload["identity_timelines"]) == 12
    assert payload["identity_timelines_truncated"] is True
    assert payload["authoritative"] is False

    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    assert "identity_timelines_truncated:data.identity_timelines_truncated===true" in source


def test_identity_timeline_upstream_truncation_is_preserved(monkeypatch):
    """An upstream capped scan remains marked incomplete even below HTTP cap."""
    import json
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates", lambda connection, *, lease: {
        "identity_timelines": [{"event_id": "one"}],
        "identity_timelines_truncated": True,
    })
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", lambda result: {
        "event_reviews": [], "evidence_sha256": "a" * 64,
    })
    payload = json.loads(history_routes.get_reconstruction_review().body)
    assert len(payload["identity_timelines"]) == 1
    assert payload["identity_timelines_truncated"] is True


def test_review_provenance_counts_match_bounded_http_projection(monkeypatch):
    """Displayed counts describe returned evidence, not unknown upstream totals."""
    import json
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    fields = ("identity_timelines", "observed_pair_event_assignments",
              "observed_group_continuity", "observed_case_groups")
    source = {field: [{"event_id": str(i)} for i in range(30)] for field in fields}
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: source)
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda result: {"event_reviews": [], "evidence_sha256": "a" * 64})
    response = history_routes.get_reconstruction_review()
    payload = json.loads(response.body)
    for field in fields:
        cap = 12 if field == "identity_timelines" else 24
        assert len(payload[field]) == cap
        assert payload[field + "_returned_count"] == cap
        assert payload[field + "_truncated"] is True


def test_reviewer_provenance_displays_returned_counts_as_inert_text():
    from server.main import ROOT
    source = (ROOT / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    for field in ("identity_timelines", "observed_pair_event_assignments",
                  "observed_group_continuity", "observed_case_groups"):
        assert field + "_returned_count:Number.isInteger(data." + field + "_returned_count)" in source
    assert "evidence.textContent=JSON.stringify({" in source


def test_live_review_rejects_invalid_engine_evidence_without_leaking_it(monkeypatch):
    """Malformed provenance must not cause an HTTP 500 or echo source content."""
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: {"identity_timelines": [], "secret": "private"})
    def invalid(_response):
        raise ValueError("private event identifier must not escape")
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", invalid)
    with pytest.raises(HTTPException) as error:
        history_routes.get_reconstruction_review()
    assert error.value.status_code == 422
    assert error.value.detail == "Invalid reconstruction evidence"
    assert "private" not in error.value.detail


def test_saved_review_rejects_invalid_engine_evidence_before_storage_read(monkeypatch):
    """A corrupt source must never cause stale reviewer annotations to be returned."""
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: {"identity_timelines": []})
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda response: (_ for _ in ()).throw(ValueError("private source detail")))
    monkeypatch.setattr(history_routes, "read_saved_reconstruction_review",
                        lambda *args: pytest.fail("read stale labels after invalid source"))
    with pytest.raises(HTTPException) as error:
        history_routes.get_saved_reconstruction_review()
    assert error.value.status_code == 422
    assert error.value.detail == "Invalid reconstruction evidence"


def test_scoring_rejects_invalid_review_without_exposing_source_details(monkeypatch):
    """Reviewer/API validation failures must not echo event IDs or source text."""
    _setup(monkeypatch)
    from server import work_resumption
    def invalid(*args, **kwargs):
        raise ValueError("private event identifier in malformed evidence")
    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", invalid)
    from server.history_routes import ReconstructionReviewSubmission
    request = ReconstructionReviewSubmission(evidence_sha256="a" * 64, annotations=[])
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(request)
    assert error.value.status_code == 422
    assert error.value.detail == "Invalid reconstruction review submission"
    assert "private" not in error.value.detail


def test_save_rejects_invalid_review_without_persisting_or_leaking(monkeypatch):
    """Failed validation cannot persist labels or disclose private source text."""
    _setup(monkeypatch)
    from server import work_resumption
    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review",
                        lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("private source record")))
    monkeypatch.setattr(history_routes, "save_reconstruction_review",
                        lambda *args, **kwargs: pytest.fail("persisted invalid reviewer annotations"))
    from server.history_routes import ReconstructionReviewSubmission
    request = ReconstructionReviewSubmission(evidence_sha256="a" * 64, annotations=[])
    with pytest.raises(HTTPException) as error:
        history_routes.save_reconstruction_review_submission(request)
    assert error.value.status_code == 422
    assert error.value.detail == "Invalid reconstruction review submission"
    assert "private" not in error.value.detail


def test_live_reconstruction_binds_review_after_identity_and_candidate_evidence():
    """A reviewer digest must cover the complete response, not a partial scan."""
    import inspect
    from server import work_resumption
    source = inspect.getsource(work_resumption.resumption_candidates)
    timeline = source.index('result["identity_timelines"] = timelines')
    candidates = source.index('result["candidates_total"] = len(candidates)')
    final_queue = source.rindex('result["event_assignment_review"] = build_review_queue(result)')
    assert timeline < candidates < final_queue
    # The empty-session early return must also bind a review to its final notice.
    empty_branch = source.split('if not current["refs"]:', 1)[1].split('return result', 1)[0]
    assert empty_branch.index('result["notice"]') < empty_branch.index(
        'result["event_assignment_review"] = build_review_queue(result)'
    )


def test_saved_review_uses_same_truncated_timeline_digest_as_live_review(monkeypatch):
    """A saved label must be retrievable against the exact live evidence basis."""
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli

    source = {"identity_timelines": [{"id": i} for i in range(13)]}
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: source)
    seen = []

    def build(result):
        seen.append(result)
        return {"evidence_sha256": "b" * 64}

    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", build)
    monkeypatch.setattr(history_routes, "read_saved_reconstruction_review",
                        lambda grant, generation, digest: {"evidence_sha256": digest})
    response = history_routes.get_saved_reconstruction_review()
    import json
    assert json.loads(response.body)["saved"]["evidence_sha256"] == "b" * 64
    assert seen[0]["identity_timelines_truncated"] is True
    assert "identity_timelines_truncated" not in source


def test_saved_review_preserves_explicit_incomplete_timeline_flag(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli
    source = {"identity_timelines": [], "identity_timelines_truncated": True}
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: source)
    def build(result):
        assert result["identity_timelines_truncated"] is True
        return {"evidence_sha256": "c" * 64}
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue", build)
    monkeypatch.setattr(history_routes, "read_saved_reconstruction_review",
                        lambda grant, generation, digest: None)
    assert b'"saved": null' in history_routes.get_saved_reconstruction_review().body


def test_authenticated_http_reconstruction_scores_interleaved_cases_without_confirming_identity(monkeypatch):
    """Exercise the actual HTTP read + scoring entrypoints and engine scorer.

    The independent synthetic truth intentionally labels the interruption as
    unrelated to either case. This tests disagreement propagation through the
    product boundary, not held-out reconstruction accuracy.
    """
    import json
    from server import work_resumption
    from server.reconstruction_review_cli import build_review_queue

    state = _setup(monkeypatch)

    def observed_pair(issue, file_ref, current, earlier=None):
        return {
            "resource_pointers": [
                {"provider": "github", "resource_kind": "issue", "resource_ref": issue},
                {"provider": "local_file", "resource_kind": "file", "resource_ref": file_ref},
            ],
            "current_evidence_event_ids": current,
            "earlier_corroborations": earlier or [],
            "assignment_status": "observed_same_event_pair",
            "proves_same_case": False,
        }

    source = {
        "derived": True, "authoritative": False,
        "saved_history_access": "all_saved",
        "coverage": {"scan_truncated": False},
        "observed_pair_event_assignments": [
            observed_pair("case-A", "file-A", ["a-return", "interruption"],
                          [{"session_started_at": "2026-11-01T09:00:00Z",
                            "evidence_event_ids": ["a-before"]}]),
            observed_pair("case-B", "file-B", ["b-between", "interruption"]),
        ],
    }
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease, run_started_at=None: source)

    read = history_routes.get_reconstruction_review()
    assert read.headers["cache-control"] == "no-store"
    observed = json.loads(read.body)
    queue = build_review_queue(source)
    assert observed["review"]["evidence_sha256"] == queue["evidence_sha256"]
    assert queue["ambiguous_event_reviews_count"] == 1

    # Truth is fixed independently of the proposed pairs, including an
    # explicit negative interruption and a resumed earlier-session event.
    truth = {
        ("earlier:2026-11-01T09:00:00+00:00", "a-before"): [
            [["github", "issue", "case-A"], ["local_file", "file", "file-A"]]],
        ("current", "a-return"): [
            [["github", "issue", "case-A"], ["local_file", "file", "file-A"]]],
        ("current", "b-between"): [
            [["github", "issue", "case-B"], ["local_file", "file", "file-B"]]],
        ("current", "interruption"): [],
    }
    assert {(row["window"], row["event_id"]) for row in queue["event_reviews"]} == set(truth)
    request = history_routes.ReconstructionReviewSubmission(
        evidence_sha256=queue["evidence_sha256"],
        annotations=[{"window": window, "event_id": event_id, "true_resource_pairs": pairs}
                     for (window, event_id), pairs in truth.items()],
    )
    scored = history_routes.score_reconstruction_review_submission(request)
    assert scored.headers["cache-control"] == "no-store"
    result = json.loads(scored.body)
    assert (result["true_positive_pairs"], result["false_positive_pairs"],
            result["false_negative_pairs"]) == (3, 2, 0)
    assert result["explicit_negative_events_with_false_links"] == 1
    assert [row["event_id"] for row in result["event_disagreements"]] == ["interruption"]
    assert result["held_out_verified"] is False
    assert result["cross_case_contamination_measured"] is False
    assert result["real_work_accuracy_claim"] is False

    # A grant revoked after the read must deny the same evidence and labels.
    state["grant"] = {"mode": "off"}
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_review_submission(request)
    assert error.value.status_code == 403


def test_save_revocation_does_not_echo_private_permission_error(monkeypatch):
    """A revoked grant must not leak private event/source text through HTTP 403."""
    _setup(monkeypatch)
    from server import work_resumption
    from server.history_routes import ReconstructionReviewSubmission

    def revoked(*args, **kwargs):
        raise PermissionError("revoked: private event id and source content")

    monkeypatch.setattr(work_resumption, "score_live_reconstruction_review", revoked)
    monkeypatch.setattr(history_routes, "save_reconstruction_review",
                        lambda *args, **kwargs: pytest.fail("saved after revocation"))
    request = ReconstructionReviewSubmission(evidence_sha256="a" * 64, annotations=[])
    with pytest.raises(HTTPException) as error:
        history_routes.save_reconstruction_review_submission(request)
    assert error.value.status_code == 403
    assert error.value.detail == "History access changed during review"
    assert "private" not in error.value.detail


def _cohort_request():
    return history_routes.ReconstructionSourceCohortSubmission(
        evidence_sha256="a" * 64,
        source_labels=[{"window": "current", "event_id": "e1", "true_resource_pairs": []}],
    )


def test_source_cohort_route_checks_consent_before_database(monkeypatch):
    _setup(monkeypatch, grant={"mode": "off"})
    monkeypatch.setattr(history_routes, "connect", lambda: pytest.fail("unauthorized database read"))
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_source_cohort(_cohort_request())
    assert error.value.status_code == 403


def test_source_cohort_route_binds_fresh_digest_and_disclaims_holdout(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: {"derived": True})
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda result: {"evidence_sha256": "a" * 64})
    monkeypatch.setattr(reconstruction_review_cli, "score_source_event_cohort",
                        lambda result, labels: {"held_out_verified": False,
                                                "independent_labels_verified": False})
    response = history_routes.score_reconstruction_source_cohort(_cohort_request())
    assert response.headers["cache-control"] == "no-store"
    assert b'"held_out_verified": false' in response.body


def test_source_cohort_route_rejects_stale_digest_without_scoring(monkeypatch):
    _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: {"derived": True})
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda result: {"evidence_sha256": "b" * 64})
    monkeypatch.setattr(reconstruction_review_cli, "score_source_event_cohort",
                        lambda *args: pytest.fail("scored stale evidence"))
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_source_cohort(_cohort_request())
    assert error.value.status_code == 422


def test_source_cohort_route_denies_revocation_after_scoring(monkeypatch):
    state = _setup(monkeypatch)
    from server import work_resumption, reconstruction_review_cli
    monkeypatch.setattr(work_resumption, "resumption_candidates",
                        lambda connection, *, lease: {"derived": True})
    monkeypatch.setattr(reconstruction_review_cli, "build_review_queue",
                        lambda result: {"evidence_sha256": "a" * 64})
    def score(result, labels):
        state["generation"] += 1
        return {"held_out_verified": False}
    monkeypatch.setattr(reconstruction_review_cli, "score_source_event_cohort", score)
    with pytest.raises(HTTPException) as error:
        history_routes.score_reconstruction_source_cohort(_cohort_request())
    assert error.value.status_code == 403


def test_source_cohort_reviewer_disclaims_stale_inflight_labels():
    """The browser must not present metrics for labels edited during an HTTP request."""
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / "dashboard" / "history_retention.js").read_text(encoding="utf-8")
    function = source.split("async function scoreReconstructionSourceCohort(){", 1)[1].split(
        "async function scoreReconstructionEvidence(){", 1)[0]
    assert "const submittedLabels=root.querySelector('#reconstructionCohortLabels').value;" in function
    assert "root.querySelector('#reconstructionCohortLabels').value!==submittedLabels" in function
    assert function.index("value!==submittedLabels") < function.index("output.textContent=JSON.stringify(result")
    assert "Source labels changed while scoring" in function
