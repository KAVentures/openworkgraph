"""Contract checks for the authenticated, read-only shared handoff preflight UI."""
from pathlib import Path

PAGE = Path(__file__).resolve().parents[1] / "gateway" / "processes.html"


def test_handoff_preflight_is_inside_authenticated_selected_process():
    html = PAGE.read_text(encoding="utf-8")
    selected = html.index('<section id="selected" hidden>')
    preflight = html.index('id="handoffPreflight"')
    end = html.index('</section>', selected)
    assert selected < preflight < end
    assert 'id="handoffPacket"' in html
    assert 'id="handoffStatus" role="status" aria-live="polite"' in html


def test_handoff_preflight_reads_fresh_authorized_process_without_upload():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('function resetHandoff(message=', 1)[1].split('      })();', 1)[0]
    assert 'await call("/v1/human/processes/"+encodeURIComponent(p.workflow_id))' in block
    assert 'fetch(' not in block
    assert 'file.text()' in block
    assert 'file.size>262144' in block
    assert 'current.latest_revision!==p.revision' in block
    assert 'current.process.content_sha256!==p.content_sha256' in block
    assert 'current.archived' in block
    assert 'current.review?.status!=="reviewed_design"' in block
    assert "lacks independent design review of this revision" in block
    assert 'ticket!==handoffGeneration||identityTicket!==identityGeneration||\n               credential!==authorizedCredential||selected!==chosen' in block
    assert 'execution_authorized!==false' in block
    assert 'source_read_authorized!==false' in block


def test_handoff_preflight_invalidates_on_session_and_selection_changes():
    html = PAGE.read_text(encoding="utf-8")
    assert 'function clear(releaseBusy=false) {\n          processOpenGeneration++;\n          resetHandoff(' in html
    assert 'function render(data) {\n          $("handoffConsent").checked=false;\n          resetHandoff(' in html
    assert 'resetHandoff("Session cleared. Recheck after sign-in.");\n          $("handoffPacket").value="";\n          $("handoffConsent").checked=false;' in html
    assert 'handoffPacket").addEventListener("change",()=>{ $("handoffConsent").checked=false; resetHandoff(' in html
    assert 'handoffConsent").addEventListener("change",()=>resetHandoff(' in html
    assert 'if(!$("handoffConsent").checked){fail("Explicit authorization to inspect this packet is required.");return;}' in html
    assert 'ticket!==handoffGeneration||identityTicket!==identityGeneration||\n               credential!==authorizedCredential||selected!==chosen||!$("handoffConsent").checked' in html
    assert 'Consent, owner approval, source access, retention and execution authorization remain unverified.' in html


def test_handoff_state_initialized_before_oidc_callback_restores_session():
    html = PAGE.read_text(encoding="utf-8")
    # The callback invokes run(connectIdentity), which synchronously calls clear().
    # resetHandoff must not touch a lexical binding in its temporal dead zone.
    assert html.index("let handoffGeneration=0;") < html.index("function clear(releaseBusy=false)")
    assert html.index("function clear(releaseBusy=false)") < html.index('run(() => connectIdentity(incomingSession, "OWG-Process-Session"))')


def test_handoff_match_never_grants_execution_or_source_access():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('function resetHandoff(message=', 1)[1].split('      })();', 1)[0]
    assert 'current.review?.status!=="reviewed_design"' in block
    assert 'source_read_authorized!==false' in block
    assert 'execution_authorized!==false' in block
    assert 'Eligible only for further human review.' in block
    assert 'Consent, owner approval, source access, retention and execution authorization remain unverified.' in block


def test_verified_handoff_only_navigates_to_existing_revision_review():
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="handoffReview" type="button" disabled' in html
    assert 'id="revisionReview">Revision review' in html
    assert '$("handoffReview").disabled=true;' in html
    assert '$("handoffReview").disabled=false;' in html
    assert 'if($("handoffReview").disabled||!selected||!credential||!$("handoffConsent").checked||!inspectedHandoff)return;' in html
    assert '$("revisionReview").scrollIntoView({block:"start"});' in html
    assert '$("comment").focus();' in html
    assert html.index('$("handoffReview").disabled=false;') > html.index('current.review?.status!=="reviewed_design"')


def test_review_navigation_revalidates_fresh_authorized_revision_and_fails_closed():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('let inspectedHandoff=null;', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'inspectedHandoff=null;' in block
    assert 'const current=await call("/v1/human/processes/"+encodeURIComponent(inspected.workflow_id));' in block
    assert 'current.latest_revision!==inspected.revision' in block
    assert 'current.process.content_sha256!==inspected.content_sha256' in block
    assert 'current.review?.status!=="reviewed_design"' in block
    assert 'ticket!==handoffGeneration||identityTicket!==identityGeneration||\n               credential!==authorizedCredential||selected!==chosen||!$("handoffConsent").checked' in block
    assert 'resetHandoff("REFUSED: Process changed or access/review was withdrawn.' in block
    assert 'resetHandoff("REFUSED: Could not revalidate authorized revision.' in block
    assert block.index('const current=await call(') < block.index('$("revisionReview").scrollIntoView')
    assert 'inspectedHandoff={workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256};' in html


def test_review_navigation_rejects_inflight_identity_and_credential_changes():
    """An old authenticated response must not navigate under a new session."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffReview").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'const identityTicket=identityGeneration, authorizedCredential=credential;' in block
    assert 'identityTicket!==identityGeneration' in block
    assert 'credential!==authorizedCredential' in block
    assert block.index('const current=await call(') < block.index('identityTicket!==identityGeneration')
    assert block.index('identityTicket!==identityGeneration') < block.index('$("revisionReview").scrollIntoView')
    assert 'resetHandoff("REFUSED: Could not revalidate authorized revision.' in block


def test_packet_inspection_rejects_inflight_identity_and_credential_changes():
    """A packet read started under an old login must not enable review navigation."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffCheck").addEventListener("click",', 1)[1].split("      })();", 1)[0]
    assert "const identityTicket=identityGeneration, authorizedCredential=credential;" in block
    assert block.count("identityTicket!==identityGeneration") >= 2
    assert block.count("credential!==authorizedCredential") >= 2
    assert block.index("identityTicket!==identityGeneration") < block.index("const p=packet?.process;")
    assert block.rindex("identityTicket!==identityGeneration") > block.index('const current=await call(')
    assert block.rindex("identityTicket!==identityGeneration") < block.index('$("handoffReview").disabled=false;')


def test_review_submission_revalidates_inspected_handoff_before_post():
    """An inspected handoff cannot be reviewed after revocation or revision drift."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'if (inspectedHandoff) {' in block
    assert 'const current=await call("/v1/human/processes/"+encodeURIComponent(pinned.workflow_id));' in block
    for guard in (
        "ticket!==handoffGeneration", "identityTicket!==identityGeneration",
        "credential!==authorizedCredential", "selected!==chosen",
        'current.review?.status!=="reviewed_design"',
        "current.latest_revision!==pinned.revision",
        "current.process.content_sha256!==pinned.content_sha256",
        "record.content_sha256!==pinned.content_sha256",
    ):
        assert guard in block
    assert block.index("const current=await call(") < block.index('"/reviews"')
    assert 'resetHandoff("REFUSED: Reviewed handoff is no longer current or authorized.' in block
