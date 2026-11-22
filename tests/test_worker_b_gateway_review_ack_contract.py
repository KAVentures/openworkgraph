"""Worker B regression: Gateway must not acknowledge an unverified review.

This is a source contract guard, NOT the required API-backed browser acceptance.
"""
from pathlib import Path


def test_review_ack_requires_authenticated_actor_and_decision():
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    start = source.index("const acknowledged=reviewResult?")
    end = source.index("if(!acknowledged)", start)
    verification = source[start:end]
    assert "reviewResult.your_actor_id===reviewSelection.your_actor_id" in verification
    assert "Array.isArray(reviewResult.review?.reviews)" in verification
    assert "entry?.reviewer_actor_id===reviewSelection.your_actor_id" in verification
    assert "entry.decision===decision" in verification
    assert "reviewResult.process.content_sha256===record.content_sha256" in verification
    assert end < source.index("render(recordedReview)", start)


def test_interrupted_review_submission_requires_fresh_user_confirmation():
    """A lost POST response is ambiguous: never allow blind retry."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    start = source.index("async function review(decision)")
    end = source.index("const acknowledged=reviewResult?", start)
    submission = source[start:end]
    assert 'reviewMutationInFlight = true' in submission
    assert 'const submittedInspectedHandoff=Boolean(inspectedHandoff)' in submission
    assert 'resetHandoff("Review submitted; outcome may be unknown' in submission
    assert '"/reviews",' in submission
    failure = submission[submission.index("} catch (error) {", submission.index("reviewResult=await call(")):]
    assert '$("reviewConfirm").checked=false' in failure
    assert "Review submission outcome is uncertain." in failure
    assert "Reopen the latest authorized revision" in failure
    assert 'encodeURIComponent(record.workflow_id)' in submission


def test_acknowledged_review_consumes_explicit_confirmation():
    """Successful submissions must not leave a reusable approval checkbox."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    start = source.index("if(!acknowledged)")
    end = source.index("render(recordedReview)", start)
    success = source[start:end]
    assert '$("reviewConfirm").checked=false' in success
    assert success.index('$("reviewConfirm").checked=false', success.index("return;")) < success.index('$("handoffStatus").textContent="Design review acknowledged.')


def test_revoked_or_stale_inspected_review_consumes_human_confirmation():
    """A denied pre-POST authorization read must require deliberate reconfirmation."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    read_failure = block.split("current=await call(", 1)[1].split("throw error;", 1)[0]
    assert '$("reviewConfirm").checked=false;' in read_failure
    stale = block.split('resetHandoff("REFUSED: Authorization read failed.', 1)[1]
    stale = stale.split('resetHandoff("REFUSED: Reviewed handoff is no longer current', 1)[0]
    assert '$("reviewConfirm").checked=false;' in stale
    assert block.index('resetHandoff("REFUSED: Reviewed handoff is no longer current') < block.index('"/reviews"')


def test_agent_project_export_revalidates_latest_reviewed_revision():
    """Executable SDK downloads require a fresh authenticated Gateway read."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function download(format) {", 1)[1].split("const response = await fetch(", 1)[0]
    assert 'if (format === "agent-project")' in block
    assert 'const current = await call("/v1/human/processes/"' in block
    for guard in (
        "identityTicket !== identityGeneration",
        "credential !== authorizedCredential",
        "selected !== selection",
        "current.archived",
        "current.viewing_historical_revision",
        "current.process.workflow_id !== record.workflow_id",
        "current.process.revision !== record.revision",
        "current.latest_revision !== record.revision",
        "current.process.content_sha256 !== record.content_sha256",
        'current.review?.status !== "reviewed_design"',
    ):
        assert guard in block
    # Compare against the refusal belonging to this guard, not the earlier
    # missing-selection refusal at the beginning of the same function.
    review_guard = block.index('current.review?.status !== "reviewed_design"')
    assert review_guard < block.index("throw new Error", review_guard)


def test_executable_export_requires_bounded_complete_zip_central_directory():
    """Reject HTML, truncated ZIP, empty ZIP and oversized downloads before exposing source."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function download(format) {", 1)[1].split("const url = URL.createObjectURL(exportedBlob)", 1)[0]
    for guard in (
        "exportedBlob.size < 22",
        "exportedBlob.size > maxArchiveBytes",
        "exportedBlob.size - 65557",
        "view.getUint32(i, true) !== 0x06054b50",
        "i + 22 + commentLength !== tail.length",
        "totalEntries > 0",
        "diskEntries === totalEntries",
        "directoryOffset + directorySize <= eocdOffset",
        "!zipHeader || !directoryValid",
        "identityTicket !== identityGeneration",
    ):
        assert guard in block
    assert block.index("!zipHeader || !directoryValid") < len(block)


def test_executable_export_encodes_workflow_identity_in_authenticated_route():
    """Workflow IDs are opaque; they must not alter export path or query parameters."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function download(format) {", 1)[1].split("const response = await fetch(", 1)[1].split("if (!response.ok)", 1)[0]
    assert 'encodeURIComponent(record.workflow_id) +' in block
    assert 'record.workflow_id +' not in block
    assert 'new URLSearchParams({' in block


def test_review_recovery_is_authenticated_read_only_and_consumes_consent():
    """A lost review response may already be committed: recovery must GET, not POST."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    assert 'id="reviewRecover" type="button"' in source
    block = source.split('$("reviewRecover").onclick', 1)[1].split('$("approve").onclick', 1)[0]
    assert 'if (!selected || !credential)' in block
    assert '$("reviewConfirm").checked = false' in block
    assert '$("handoffConsent").checked = false' in block
    assert 'resetHandoff(' in block
    assert 'await open(workflowId)' in block
    assert 'review("reviewed_design")' not in block
    assert '"/reviews"' not in block
    open_block = source.split("async function open(id, revision)", 1)[1].split("function render(data)", 1)[0]
    assert 'encodeURIComponent(id)' in open_block
    assert 'render(data)' in open_block



def test_direct_review_preflight_rechecks_live_gateway_permission_and_revision():
    """Direct review cannot rely on stale permissions from a previously rendered form."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function review(decision) {", 1)[1].split('"/reviews",', 1)[0]
    direct = block.split("if (!inspectedHandoff) {", 1)[1].split("// Do not let an old review response", 1)[0]
    assert 'await call("/v1/human/processes/"+encodeURIComponent(record.workflow_id))' in direct
    for guard in (
        "identityTicket!==identityGeneration",
        "credential!==authorizedCredential",
        "selected!==chosen",
        "current.archived",
        "current.viewing_historical_revision",
        "current.latest_revision!==record.revision",
        "current.process.content_sha256!==record.content_sha256",
        "current.access_revision!==chosen.access_revision",
        "current.permissions?.review!==true",
        "current.your_actor_id!==chosen.your_actor_id",
    ):
        assert guard in direct
    assert '$("reviewConfirm").checked=false' in direct
    assert "throw new Error" in direct


def test_review_recovery_denial_hides_stale_actionable_process_and_never_replays_post():
    """Denied recovery cannot leave a revoked review form looking authorized."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split('$("reviewRecover").onclick =', 1)[1].split('$("approve").onclick', 1)[0]
    assert 'const recoveryOpenTicket = processOpenGeneration + 1;' in block
    assert 'await open(workflowId);' in block
    assert 'catch (error)' in block
    assert 'identityTicket === identityGeneration' in block
    assert 'credential === authorizedCredential' in block
    assert 'recoveryOpenTicket === processOpenGeneration' in block
    assert 'selected = null;' in block
    assert '$("selected").hidden = true;' in block
    assert 'REFUSED: Could not reopen an authorized review.' in block
    assert 'recoveryOpenTicket !== processOpenGeneration' in block
    assert 'selected?.process?.workflow_id !== workflowId' in block
    assert '$("reviewConfirm").checked = false;' in block
    assert '"/reviews"' not in block
    assert block.index('await open(workflowId);') < block.index('Latest authorized review loaded.')


def test_direct_review_denied_gateway_preflight_disarms_confirmation():
    """A failed live authorization read cannot leave a direct review ready to retry."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("if (!inspectedHandoff) {", 1)[1].split("// Do not let an old review response", 1)[0]
    assert 'current=await call("/v1/human/processes/"+encodeURIComponent(record.workflow_id));' in block
    assert 'catch (error)' in block
    assert 'identityTicket===identityGeneration' in block
    assert 'credential===authorizedCredential && selected===chosen' in block
    assert '$("reviewConfirm").checked=false;' in block
    assert 'Could not verify current review access.' in block
    assert 'throw error;' in block
    assert '"/reviews"' not in block
