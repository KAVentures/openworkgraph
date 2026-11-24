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


def test_denied_review_preflight_invalidates_entire_selected_process():
    """Denied live access must disable editing, exporting and review, not only the checkbox."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    helper = source.split("function invalidateDeniedReviewSelection(message) {", 1)[1].split("let reviewMutationInFlight", 1)[0]
    for guard in ('processOpenGeneration++', 'selected = null', '$("selected").hidden = true',
                  '$("reviewConfirm").checked = false', '$("handoffConsent").checked = false',
                  'resetHandoff('):
        assert guard in helper
    block = source.split("async function review(decision) {", 1)[1].split('"/reviews",', 1)[0]
    assert block.count("invalidateDeniedReviewSelection(") == 2
    assert 'resetHandoff("REFUSED: Reviewed handoff is no longer current' in block


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


def test_direct_review_denied_gateway_preflight_disarms_selection():
    """A failed live authorization read cannot leave any process action enabled."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("if (!inspectedHandoff) {", 1)[1].split("// Do not let an old review response", 1)[0]
    assert 'current=await call("/v1/human/processes/"+encodeURIComponent(record.workflow_id));' in block
    assert 'catch (error)' in block
    assert 'identityTicket===identityGeneration' in block
    assert 'credential===authorizedCredential && selected===chosen' in block
    assert 'invalidateDeniedReviewSelection("Could not verify current review access.' in block
    assert 'throw error;' in block
    assert '"/reviews"' not in block


def test_denied_packet_refresh_hides_revoked_review_actions():
    """A failed Gateway refresh must not leave a stale actionable review form."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split('$("handoffRefresh").addEventListener', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'await open(workflowId)' in block
    failure = block.split('catch (error)', 1)[1].split('throw error;', 1)[0]
    for guard in (
        'recoveryOpenTicket===processOpenGeneration',
        'identityTicket===identityGeneration',
        'selected?.process?.workflow_id===workflowId',
        'selected=null;',
        '$("selected").hidden=true;',
        '$("reviewConfirm").checked=false;',
        'REFUSED: Could not reopen an authorized revision.',
    ):
        assert guard in failure
    assert '"/reviews"' not in block


def test_review_generation_is_rechecked_across_inspection_navigation_and_submission():
    """A concurrent decision by this reviewer invalidates an older reviewed handoff."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    expected = "current.your_review_number!==chosen.your_review_number"
    # Each independent live Gateway read must pin the review generation:
    # inspected submission, direct submission, navigation, and packet check.
    assert source.count(expected) == 4
    inspected = source.split("if (inspectedHandoff) {", 1)[1].split("if (!inspectedHandoff) {", 1)[0]
    direct = source.split("if (!inspectedHandoff) {", 1)[1].split("// Do not let an old review response", 1)[0]
    navigation = source.split('$("handoffReview").addEventListener', 1)[1].split('$("handoffRefresh").addEventListener', 1)[0]
    packet = source.split('$("handoffCheck").addEventListener', 1)[1]
    for block in (inspected, direct, navigation, packet):
        assert expected in block
        assert 'current.permissions?.review!==true' in block


def test_browser_history_restore_consumes_handoff_and_review_confirmation():
    """BFCache restoration must not reuse an earlier human authorization."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    for event in ('"pagehide"', '"pageshow"'):
        block = source.split('window.addEventListener(' + event, 1)[1].split('});', 1)[0]
        assert '$("handoffConsent").checked=false' in block
        assert '$("reviewConfirm").checked=false' in block
        assert 'resetHandoff(' in block
    assert 'if(!event.persisted)return;' in source.split('window.addEventListener("pageshow"', 1)[1].split('});', 1)[0]


def test_bfcache_restore_invalidates_selected_process_and_pending_reads():
    """A restored page must not allow direct review, edits or export from stale access."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split('window.addEventListener("pageshow"', 1)[1].split('});', 1)[0]
    assert 'if(!event.persisted)return;' in block
    assert 'processOpenGeneration++;' in block
    assert 'selected=null;' in block
    assert '$("selected").hidden=true;' in block
    assert '$("reviewConfirm").checked=false;' in block
    assert '$("handoffConsent").checked=false;' in block
    assert 'resetHandoff(' in block


def test_handoff_review_navigation_denial_disarms_all_process_controls():
    """A failed live preflight must not leave direct edit/review/export available."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    start = source.index('$("handoffReview").addEventListener("click"')
    end = source.index('$("handoffRefresh").addEventListener("click"', start)
    navigation = source[start:end]
    assert 'invalidateDeniedReviewSelection("Review navigation revalidation failed.' in navigation
    assert 'identityTicket===identityGeneration&&' in navigation
    assert 'credential===authorizedCredential&&selected===chosen' in navigation
    helper = source.split("function invalidateDeniedReviewSelection(message) {", 1)[1].split("let reviewMutationInFlight", 1)[0]
    assert 'selected = null' in helper
    assert '$("selected").hidden = true' in helper


def test_stale_handoff_navigation_disarms_direct_process_actions():
    """A successful Gateway read with changed access/revision is still a denial.

    A stale selected process must not remain editable or reviewable when the
    authenticated navigation preflight returns a conflicting revision/ACL.
    """
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    navigation = source.split('$("handoffReview").addEventListener("click"', 1)[1].split(
        '$("handoffRefresh").addEventListener("click"', 1
    )[0]
    stale = navigation.split('current.permissions?.review!==true', 1)[1].split('return;', 1)[0]
    assert 'invalidateDeniedReviewSelection("Review navigation rejected:' in stale
    assert 'resetHandoff("REFUSED: Process changed' not in stale
    helper = source.split("function invalidateDeniedReviewSelection(message) {", 1)[1].split(
        "let reviewMutationInFlight", 1
    )[0]
    assert "selected = null;" in helper
    assert '$("selected").hidden = true;' in helper
    assert '$("reviewConfirm").checked = false;' in helper
    assert '$("handoffConsent").checked = false;' in helper
