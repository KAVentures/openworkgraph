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
    assert 'current.permissions?.review!==true' in block
    assert 'invalidateDeniedReviewSelection("Inspection refused: current process authorization or revision changed.' in block
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
    assert html.index("let inspectedHandoff=null;") < html.index("function clear(releaseBusy=false)")
    assert html.count("let inspectedHandoff=null;") == 1
    assert html.index("function clear(releaseBusy=false)") < html.index('run(() => connectIdentity(incomingSession, "OWG-Process-Session"))')


def test_handoff_match_never_grants_execution_or_source_access():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('function resetHandoff(message=', 1)[1].split('      })();', 1)[0]
    assert 'current.permissions?.review!==true' in block
    assert 'source_read_authorized!==false' in block
    assert 'execution_authorized!==false' in block
    assert 'Eligible for human design review only; independent approval and execution are not granted by inspection.' in block
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
    assert html.index('$("handoffReview").disabled=false;') > html.index('current.permissions?.review!==true')


def test_review_navigation_revalidates_fresh_authorized_revision_and_fails_closed():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('let inspectedHandoff=null;', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'inspectedHandoff=null;' in block
    assert 'const current=await call("/v1/human/processes/"+encodeURIComponent(inspected.workflow_id));' in block
    assert 'current.latest_revision!==inspected.revision' in block
    assert 'current.process.content_sha256!==inspected.content_sha256' in block
    assert 'current.permissions?.review!==true' in block
    assert 'ticket!==handoffGeneration||identityTicket!==identityGeneration||\n               credential!==authorizedCredential||selected!==chosen||!$("handoffConsent").checked' in block
    assert 'invalidateDeniedReviewSelection("Review navigation rejected: process revision or review permission changed.' in block
    assert 'invalidateDeniedReviewSelection("Review navigation revalidation failed.' in block
    navigation = block.split('$("handoffReview").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert navigation.index('const current=await call(') < navigation.index('$("revisionReview").scrollIntoView')
    assert 'inspectedHandoff={workflow_id:p.workflow_id,revision:p.revision,content_sha256:p.content_sha256,access_revision:current.access_revision};' in html


def test_review_navigation_rejects_inflight_identity_and_credential_changes():
    """An old authenticated response must not navigate under a new session."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffReview").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'const identityTicket=identityGeneration, authorizedCredential=credential;' in block
    assert 'identityTicket!==identityGeneration' in block
    assert 'credential!==authorizedCredential' in block
    assert block.index('const current=await call(') < block.index('identityTicket!==identityGeneration')
    assert block.index('identityTicket!==identityGeneration') < block.index('$("revisionReview").scrollIntoView')
    assert 'invalidateDeniedReviewSelection("Review navigation revalidation failed.' in block


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
    assert 'current=await call("/v1/human/processes/"+encodeURIComponent(pinned.workflow_id));' in block
    assert "let current;" in block
    for guard in (
        "ticket!==handoffGeneration", "identityTicket!==identityGeneration",
        "credential!==authorizedCredential", "selected!==chosen",
        "current.permissions?.review!==true",
        "current.latest_revision!==pinned.revision",
        "current.process.content_sha256!==pinned.content_sha256",
        "record.content_sha256!==pinned.content_sha256",
        "chosen.archived||chosen.viewing_historical_revision",
        "current.process.workflow_id!==pinned.workflow_id",
    ):
        assert guard in block
    assert block.index("current=await call(") < block.index('"/reviews"')
    assert 'resetHandoff("REFUSED: Reviewed handoff is no longer current or authorized.' in block



def test_delayed_review_response_cannot_restore_another_session_or_process():
    """A successful POST must not repaint the UI after sign-out or selection drift."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert "const reviewIdentity=identityGeneration, reviewCredential=credential;" in block
    assert "const reviewSelection=selected;" in block
    assert "reviewResult=await call(" in block
    assert "reviewIdentity!==identityGeneration||reviewCredential!==credential||" in block
    assert "selected!==reviewSelection)return;" in block
    assert block.index("reviewResult=await call(") < block.index("selected!==reviewSelection)return;")
    assert block.index("selected!==reviewSelection)return;") < block.index("render(recordedReview);")


def test_review_submission_rejects_confirmation_or_comment_drift_during_revalidation():
    """The decision and human confirmation cannot change during the API read."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'const confirmedReview = $("reviewConfirm").checked;' in block
    assert 'const confirmedComment = $("comment").value;' in block
    assert '!$("reviewConfirm").checked||!confirmedReview' in block
    assert '$("comment").value!==confirmedComment' in block
    assert 'comment: confirmedComment,' in block
    assert block.index('current=await call(') < block.index('$("comment").value!==confirmedComment')
    assert block.index('$("comment").value!==confirmedComment') < block.index('"/reviews"')


def test_review_authorization_read_failure_invalidates_inspected_packet():
    """Denied or unavailable Gateway read cannot retain a reusable inspected handoff."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'current=await call("/v1/human/processes/"+encodeURIComponent(pinned.workflow_id));' in block
    assert 'catch (error)' in block
    assert 'ticket===handoffGeneration && identityTicket===identityGeneration' in block
    assert 'credential===authorizedCredential && selected===chosen' in block
    assert 'invalidateDeniedReviewSelection("Review access could not be verified.' in block
    assert 'throw error;' in block
    assert block.index('invalidateDeniedReviewSelection("Review access could not be verified.') < block.index('"/reviews"')


def test_failed_review_navigation_cannot_invalidate_newer_session():
    """A rejected old Gateway request must not clear a newly selected identity/process."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffReview").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert "if(ticket===handoffGeneration&&identityTicket===identityGeneration&&" in block
    assert "credential===authorizedCredential&&selected===chosen)" in block
    assert block.index("credential===authorizedCredential&&selected===chosen)") < block.index('invalidateDeniedReviewSelection("Review navigation revalidation failed.')


def test_stale_review_authorization_response_does_not_clear_new_handoff():
    """An old Gateway read must not invalidate a later session or inspection."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    stale = "if(ticket!==handoffGeneration||identityTicket!==identityGeneration||"
    assert stale in block
    assert "credential!==authorizedCredential||selected!==chosen)return;" in block
    assert block.index(stale) < block.index('resetHandoff("REFUSED: Reviewed handoff is no longer current')
    # Server-side revision/review denial still invalidates the CURRENT inspection.
    assert 'current.permissions?.review!==true' in block
    assert 'current.process.content_sha256!==pinned.content_sha256' in block


def test_successful_review_consumes_inspected_handoff_before_render():
    """A completed human decision must not leave a reusable inspected packet."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'if (inspectedHandoff) {' in block
    assert 'resetHandoff("Review submitted; outcome may be unknown' in block
    assert '$("handoffConsent").checked=false;' in block
    assert block.index('resetHandoff("Review submitted; outcome may be unknown') < block.index('reviewResult=await call(')
    assert block.index('selected!==reviewSelection)return;') < block.index('render(recordedReview);')
    assert block.index('render(recordedReview);') < block.index('note("Design review acknowledged')


def test_handoff_recovery_reopens_latest_using_authenticated_gateway_and_requires_new_consent():
    """Stale/denied handoff recovery must not reuse a previously inspected packet."""
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="handoffRefresh" type="button"' in html
    block = html.split('$("handoffRefresh").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'if(!selected||!credential)' in block
    assert 'const workflowId=selected.process?.workflow_id;' in block
    assert '$("handoffConsent").checked=false;' in block
    assert 'resetHandoff("Reopening latest authorized revision.' in block
    assert 'await open(workflowId);' in block
    assert 'identityTicket!==identityGeneration' in block
    assert 'selected.process?.workflow_id!==workflowId' in block
    assert block.index('resetHandoff(') < block.index('await open(workflowId);')
    # open() performs a fresh authenticated API read and renders the returned revision.
    opener = html.split('async function open(id, revision) {', 1)[1].split('function render(data)', 1)[0]
    assert 'const data = await call(' in opener
    assert '"/v1/human/processes/" + encodeURIComponent(id) + params' in opener
    assert 'render(data);' in opener
    assert 'openTicket !== processOpenGeneration' in opener
    assert 'identityTicket !== identityGeneration' in opener
    assert 'credential !== authorizedCredential' in opener


def test_recovery_completion_rejects_superseding_open_of_same_process():
    """A second same-ID navigation must not let the first recovery report success."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffRefresh").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert "const recoveryOpenTicket=processOpenGeneration+1;" in block
    assert "recoveryOpenTicket!==processOpenGeneration" in block
    assert block.index("await open(workflowId);") < block.index("recoveryOpenTicket!==processOpenGeneration")
    assert block.index("recoveryOpenTicket!==processOpenGeneration") < block.index("Latest authorized revision reopened.")


def test_denied_recovery_reports_failure_only_for_current_identity_and_open():
    """Denied Gateway reads cannot appear to have reopened an authorized revision."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffRefresh").addEventListener("click",', 1)[1].split('$("handoffPacket").addEventListener', 1)[0]
    assert 'try {\n            await open(workflowId);' in block
    assert 'recoveryOpenTicket===processOpenGeneration' in block
    assert 'identityTicket===identityGeneration' in block
    assert 'REFUSED: Could not reopen an authorized revision.' in block
    assert block.index('REFUSED: Could not reopen') < block.index('throw error;')
    assert block.index('throw error;') < block.index('Latest authorized revision reopened.')


def test_review_submission_consumes_inspection_before_uncertain_post():
    """A committed POST with a lost response cannot reuse prior inspection consent."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'const submittedInspectedHandoff=Boolean(inspectedHandoff);' in block
    assert 'resetHandoff("Review submitted; outcome may be unknown' in block
    assert '$("handoffConsent").checked=false;' in block
    assert block.index('resetHandoff("Review submitted; outcome may be unknown') < block.index('reviewResult=await call(')
    assert block.index('$("handoffConsent").checked=false;') < block.index('reviewResult=await call(')
    assert 'if (submittedInspectedHandoff)' in block
    assert 'render(recordedReview);' in block


def test_stale_packet_read_failure_cannot_overwrite_new_identity_or_selection():
    """A failed old packet read cannot report denial in a newer session."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffCheck").addEventListener("click",', 1)[1]
    assert 'const identityTicket=identityGeneration, authorizedCredential=credential;' in block
    assert 'ticket===handoffGeneration&&identityTicket===identityGeneration' in block
    assert 'credential===authorizedCredential&&selected===chosen' in block
    assert 'invalidateDeniedReviewSelection("Inspection authorization could not be verified.' in block



def test_review_acknowledgment_is_revision_pinned_and_not_execution_receipt():
    """The Gateway UI must not imply that a design review executed agent work."""
    html = PAGE.read_text(encoding="utf-8")
    assert 'id="reviewOutcome" role="status" aria-live="polite"' in html
    render = html.split("function render(data) {", 1)[1].split("async function review(decision) {", 1)[0]
    assert '$("reviewOutcome").textContent = "";' in render
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'reviewResult?.process?.workflow_id===record.workflow_id' in block
    assert 'reviewResult.process.revision===record.revision' in block
    assert 'reviewResult.process.content_sha256===record.content_sha256' in block
    assert 'Human design review only; no agent handoff or execution occurred.' in block
    assert 'revision reference could not be verified' in block
    assert block.index("render(recordedReview);") < block.index('$("reviewOutcome").textContent =')


def test_review_mismatched_gateway_response_never_reports_success():
    """A successful HTTP POST with mismatched revision is an unknown outcome."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'reviewResult?.process?.workflow_id===record.workflow_id' in block
    assert 'reviewResult.process.revision===record.revision' in block
    assert 'reviewResult.process.content_sha256===record.content_sha256' in block
    assert 'Review outcome unverified. Reopen the latest authorized revision' in block
    assert 'Design review acknowledged against the exact revision and content hash.' in block
    assert 'Design review recorded against the exact revision and content hash.' not in block


def test_unverified_review_response_cannot_render_another_workflow():
    """HTTP success is insufficient to replace selected process or claim review."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert "const acknowledged=reviewResult?.process?.workflow_id===record.workflow_id" in block
    assert "reviewResult.process.revision===record.revision" in block
    assert "reviewResult.process.content_sha256===record.content_sha256" in block
    assert 'if(!acknowledged){' in block
    assert '$("reviewConfirm").checked=false;' in block
    assert "check its review state before resubmitting" in block
    assert block.index("if(!acknowledged){") < block.index("render(recordedReview);")
    assert block.index("return;", block.index("if(!acknowledged){")) < block.index("render(recordedReview);")


def test_concurrent_review_mutations_are_serialized_and_lost_response_stays_fail_closed():
    """Double-clicks cannot submit concurrent decisions; a failed POST never restores consent."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert "let reviewMutationInFlight = false;" in html
    assert 'if (reviewMutationInFlight) throw new Error(' in block
    assert "reviewMutationInFlight = true;" in block
    assert "let reviewResult;" in block
    assert "reviewResult=await call(" in block
    assert block.index("reviewMutationInFlight = true;") < block.index("current=await call(")
    assert "reviewMutationInFlight = false;" in block
    assert block.index("reviewMutationInFlight = true;") < block.index('"/reviews"')
    assert block.index("reviewMutationInFlight = false;") > block.index('"/reviews"')
    assert 'resetHandoff("Review submitted; outcome may be unknown' in block
    assert block.index("reviewMutationInFlight = true;") < block.index('resetHandoff("Review submitted; outcome may be unknown')
    assert '$("handoffConsent").checked=false;' in block


def test_review_success_status_requires_verified_actor_acknowledgment():
    """An HTTP 200 alone must never announce a completed human review."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'reviewResult.your_actor_id===reviewSelection.your_actor_id' in block
    assert 'entry?.reviewer_actor_id===reviewSelection.your_actor_id' in block
    assert 'entry.decision===decision' in block
    assert 'Review response received; verifying acknowledgment.' in block
    assert 'Design review acknowledged. Reopen and reinspect' in block
    assert block.index('if(!acknowledged){') < block.index('Design review acknowledged. Reopen and reinspect')


def test_review_acknowledgment_rejects_ambiguous_duplicate_actor_entries():
    """A matching historical/duplicate decision cannot prove the latest decision."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'reviewResult.review.reviews.filter((entry)=>' in block
    assert 'entry?.reviewer_actor_id===reviewSelection.your_actor_id).length===1' in block
    assert 'entry.decision===decision' in block
    assert block.index(').length===1') < block.index('reviewResult.review.reviews.some((entry)=>')
    assert block.index('if(!acknowledged){') < block.index('render(recordedReview);')


def test_handoff_live_reads_bind_reviewer_permission_and_actor_identity():
    """A valid digest and review status cannot substitute for live reviewer access."""
    html = PAGE.read_text(encoding="utf-8")
    review = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    navigation = html.split('$("handoffReview").addEventListener("click"', 1)[1].split('$("handoffRefresh").addEventListener', 1)[0]
    inspection = html.split('$("handoffCheck").addEventListener("click"', 1)[1]
    for block in (review, navigation, inspection):
        assert "current.permissions?.review!==true" in block
        assert "current.your_actor_id!==chosen.your_actor_id" in block
        assert 'current.permissions?.review!==true' in block


def test_export_response_is_discarded_after_session_or_selection_changes():
    """An in-flight export must not download after logout, credential rotation or revision switch."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function download(format) {", 1)[1].split('$("download', 1)[0]
    assert "const identityTicket = identityGeneration, authorizedCredential = credential;" in block
    assert "const exportedBlob = await response.blob();" in block
    assert "identityTicket !== identityGeneration" in block
    assert "credential !== authorizedCredential || selected !== selection" in block
    assert "selected?.process?.workflow_id !== record.workflow_id" in block
    assert "selected?.process?.revision !== record.revision" in block
    assert "selected?.process?.content_sha256 !== record.content_sha256" in block
    assert "Export canceled: session or selected revision changed" in block
    assert block.index("const exportedBlob = await response.blob();") < block.index("URL.createObjectURL(exportedBlob)")


def test_executable_sdk_export_revalidates_actor_and_access_revision():
    """A reviewed revision cannot be exported after membership changes."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function download(format) {", 1)[1].split("function resetHandoff(", 1)[0]
    assert 'current.your_actor_id !== selection.your_actor_id' in block
    assert 'current.access_revision !== selection.access_revision' in block
    assert block.index('current.access_revision !== selection.access_revision') < block.index('const response = await fetch(')


def test_acknowledged_review_requires_authenticated_durable_readback():
    """A POST response alone cannot claim that a human review was recorded."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'recordedReview=await call("/v1/human/processes/"+encodeURIComponent(record.workflow_id));' in block
    assert 'recordedReview.process.content_sha256===record.content_sha256' in block
    assert 'recordedReview.your_actor_id===reviewSelection.your_actor_id' in block
    assert 'entry.decision===decision' in block
    assert 'recordedEntries.filter(entry=>entry?.reviewer_actor_id===reviewSelection.your_actor_id).length===1' in block
    assert block.index('reviewResult=await call(') < block.index('recordedReview=await call(')
    assert block.index('recordedReview=await call(') < block.index('render(recordedReview);')


def test_durable_review_readback_failure_requires_recovery_without_retry():
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'durable Gateway read-back failed' in block
    assert 'Gateway read-back did not verify this exact review decision' in block
    assert 'reviewIdentity!==identityGeneration||reviewCredential!==credential' in block
    assert 'selected!==reviewSelection)return;' in block
    assert 'if(!recorded) {' in block
    assert block.index('if(!recorded) {') < block.index('render(recordedReview);')


def test_handoff_inspection_pins_membership_revision_for_review_navigation():
    """A content-identical membership change must invalidate an inspected packet."""
    html = PAGE.read_text(encoding="utf-8")
    inspection = html.split('$("handoffCheck").addEventListener("click"', 1)[1]
    navigation = html.split('$("handoffReview").addEventListener("click"', 1)[1].split('$("handoffRefresh").addEventListener', 1)[0]
    assert 'current.access_revision!==chosen.access_revision' in inspection
    assert 'access_revision:current.access_revision' in inspection
    assert 'current.access_revision!==inspected.access_revision' in navigation
    assert 'chosen.access_revision!==inspected.access_revision' in navigation
    assert 'current.permissions?.review!==true' in inspection
    assert 'current.permissions?.review!==true' in navigation
    assert 'current.your_actor_id!==chosen.your_actor_id' in inspection
    assert 'current.your_actor_id!==chosen.your_actor_id' in navigation


def test_handoff_review_submission_rejects_membership_revision_drift():
    """A consented, digest-identical packet must not survive a membership change at POST time."""
    html = PAGE.read_text(encoding="utf-8")
    review = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert "current.access_revision!==pinned.access_revision" in review
    assert "current.permissions?.review!==true" in review
    assert "current.your_actor_id!==chosen.your_actor_id" in review
    assert review.index("current.access_revision!==pinned.access_revision") < review.index('"/reviews"')
    assert review.index("resetHandoff(\"REFUSED: Reviewed handoff") < review.index('"/reviews"')


def test_handoff_review_readback_rejects_access_revocation_after_post():
    """A committed review cannot claim a still-authorized handoff after membership drift."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert "recordedReview.access_revision===reviewSelection.access_revision" in block
    assert "recordedReview.access_revision===reviewSelection.access_revision" in block
    assert "recordedReview.permissions?.review===true" in block
    assert block.index("recordedReview.access_revision===reviewSelection.access_revision") < block.index("if(!recorded) {")
    assert block.index("if(!recorded) {") < block.index("Design review acknowledged. Reopen and reinspect")


def test_review_readback_refuses_archived_or_superseded_revision():
    """A historical matching decision must not certify a current reviewed handoff."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('const recordedEntries=recordedReview?.review?.reviews;', 1)[1].split('if(!recorded)', 1)[0]
    assert '!recordedReview.archived' in block
    assert '!recordedReview.viewing_historical_revision' in block
    assert 'recordedReview.latest_revision===record.revision' in block
    assert 'recordedReview.process.content_sha256===record.content_sha256' in block
    assert 'recordedReview.permissions?.review===true' in block


def test_review_ui_renders_durable_gateway_readback_not_post_snapshot():
    """A stale POST response must not overwrite the independently verified review state."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    assert 'render(recordedReview);' in block
    assert 'render(reviewResult);' not in block
    assert block.index('recordedReview=await call(') < block.index('if(!recorded) {')
    assert block.index('if(!recorded) {') < block.index('render(recordedReview);')


def test_executable_project_export_revalidates_after_streaming_before_download():
    """A revocation while receiving ZIP bytes must prevent exposing the project."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("async function download(format) {", 1)[1].split("function resetHandoff(", 1)[0]
    assert block.count('await call("/v1/human/processes/" + encodeURIComponent(record.workflow_id))') == 2
    post_stream = block.split("const exportedBlob = await response.blob();", 1)[1]
    guard = post_stream.split("const url = URL.createObjectURL(exportedBlob)", 1)[0]
    assert 'if (format === "agent-project")' in guard
    assert 'latest.review?.status !== "reviewed_design"' in guard
    assert 'latest.access_revision !== selection.access_revision' in guard
    assert 'latest.your_actor_id !== selection.your_actor_id' in guard
    assert 'latest.latest_revision !== record.revision' in guard
    assert 'latest.process.content_sha256 !== record.content_sha256' in guard
    assert 'latest.archived' in guard
    assert 'latest.viewing_historical_revision' in guard
    assert 'credential !== authorizedCredential' in guard
    assert guard.index('const latest = await call(') < guard.index('latest.review?.status')


def test_executable_export_rejects_non_zip_success_payload_and_session_rotation():
    """A proxy's 200 HTML login response must never become an executable download."""
    html = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = html.split("async function download(format) {", 1)[1].split("function resetHandoff(", 1)[0]
    guard = block.split("const signature = new Uint8Array(", 1)[1].split("const url = URL.createObjectURL", 1)[0]
    assert "exportedBlob.slice(0, 4).arrayBuffer()" in guard
    assert "signature[0] === 0x50 && signature[1] === 0x4b" in guard
    assert "if (!zipHeader || !directoryValid ||" in guard
    assert "identityTicket !== identityGeneration" in guard
    assert "credential !== authorizedCredential || selected !== selection" in guard
    assert block.index("const latest = await call(") < block.index("const signature = new Uint8Array(")
    assert block.index("const signature = new Uint8Array(") < block.index("URL.createObjectURL(exportedBlob)")


def test_pending_design_review_is_not_circularly_blocked_by_preflight():
    """Review navigation may precede approval, but inspection cannot grant execution."""
    html = PAGE.read_text(encoding="utf-8")
    inspection = html.split('$("handoffCheck").addEventListener("click",', 1)[1].split("      })();", 1)[0]
    navigation = html.split('$("handoffReview").addEventListener("click",', 1)[1].split('$("handoffRefresh").addEventListener', 1)[0]
    submission = html.split("async function review(decision) {", 1)[1].split('$("approve").onclick', 1)[0]
    for block in (inspection, navigation, submission):
        assert 'current.review?.status!=="reviewed_design"' not in block
        assert 'current.permissions?.review!==true' in block
    assert "execution_authorized!==false" in inspection
    assert "independent approval and execution are not granted by inspection" in inspection
    assert 'latest.review?.status !== "reviewed_design"' in html


def test_review_readback_rejects_access_revocation_even_without_packet_inspection():
    """A direct review must not show success after membership changes mid-flight."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split("const recordedEntries=recordedReview?.review?.reviews;", 1)[1].split("if(!recorded)", 1)[0]
    assert "recordedReview.access_revision===reviewSelection.access_revision" in block
    assert "recordedReview.permissions?.review===true" in block
    assert "!submittedInspectedHandoff ||" not in block
    assert block.index("recordedReview.access_revision===reviewSelection.access_revision") < block.index("Array.isArray(recordedEntries)")


def test_authorized_reopen_disarms_prior_process_while_gateway_read_is_pending():
    """A failed or delayed live read cannot expose actions for an old selection."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function open(id, revision) {", 1)[1].split("function render(data)", 1)[0]
    before_read = block.split("const data = await call(", 1)[0]
    for guard in (
        "selected = null;",
        '$("selected").hidden = true;',
        '$("reviewConfirm").checked = false;',
        '$("handoffConsent").checked = false;',
        "resetHandoff(",
    ):
        assert guard in before_read
    assert "const authorizedCredential = credential;" in before_read
    assert "credential !== authorizedCredential" in block
    assert block.index('const data = await call(') < block.index("render(data);")



def test_history_disarms_stale_revision_navigation_after_identity_or_selection_change():
    """History links must not survive an authenticated process/tenant switch."""
    source = (Path(__file__).resolve().parents[1] / "gateway" / "processes.html").read_text()
    block = source.split("async function history(before) {", 1)[1].split('$("history").onclick', 1)[0]
    before_read = block.split("const result = await call(", 1)[0]
    after_read = block.split("const result = await call(", 1)[1]
    assert "host.replaceChildren();" in before_read
    assert "const selection = selected;" in before_read
    assert "const identityTicket = identityGeneration, authorizedCredential = credential;" in before_read
    assert "encodeURIComponent(id)" in block
    assert "ticket !== historyGeneration" in after_read
    assert "identityTicket !== identityGeneration" in after_read
    assert "credential !== authorizedCredential" in after_read
    assert "selected !== selection" in after_read
    assert "Array.isArray(result?.workflows)" in after_read
    # Both revision and pagination buttons must independently reject stale clicks.
    assert after_read.count("ticket !== historyGeneration") >= 3


def test_authenticated_packet_inspection_denial_disarms_direct_process_actions():
    """A rejected live inspection must not leave review/edit/export controls armed."""
    html = PAGE.read_text(encoding="utf-8")
    block = html.split('$("handoffCheck").addEventListener("click",', 1)[1].split("      })();", 1)[0]
    denial = block.split("if(!current?.process||", 1)[1].split("// Inspection may fetch", 1)[0]
    assert 'invalidateDeniedReviewSelection("Inspection refused:' in denial
    failure = block.split("}catch(_){", 1)[-1]
    assert "ticket===handoffGeneration" in failure
    assert "identityTicket===identityGeneration" in failure
    assert "credential===authorizedCredential" in failure
    assert "selected===chosen" in failure
    assert 'invalidateDeniedReviewSelection("Inspection authorization could not be verified.' in failure
