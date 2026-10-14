"""Swedish social actor cues, marked browser title cuts, and opt-in reference UX."""
from __future__ import annotations

from gateway.plugin_redaction import PublicRedactor
from server.contextual_redaction import Detector
from server.browser_signal_settings import public_settings
from shared.core.browser_title_privacy import cap_title_text
from shared.core.browser_privacy import sanitize_browser_page
from shared.core.identity_cues import redact_explicit_person_cues


def test_unlisted_actor_before_social_verb_redacted_local_and_hosted():
    local = Detector(token=lambda kind, name: "PERSON_PRIVATE")
    hosted = PublicRedactor(secret="synthetic-test-secret", principal="synthetic-person")
    for value in ("Katarina kommenterade fakturan", "Katarina gillade inlägget",
                  "Katarina delade en länk", "Katarina replied to review"):
        assert "Katarina" not in local.redact(value)
        assert "Katarina" not in hosted.text(value)


def test_generic_company_and_workflow_titles_not_false_person_cues():
    redact = lambda name: "PERSON_PRIVATE"
    unchanged = ("Medlink kommenterade avtal", "PR #197 · Work review", "Hej alla")
    for title in unchanged:
        assert redact_explicit_person_cues(
            title, person_token=redact, blocked_names={"medlink"}
        ) == title


def test_browser_title_cut_is_explicit_and_keeps_secret_fragments_out():
    title = "Samarbetsavtal | " + ("unchanged notes " * 10000) + " | Gmail"
    cap = cap_title_text(title, max_chars=240)
    assert len(cap) <= 240
    assert "[truncated" in cap
    assert cap.startswith("Samarbetsavtal")
    assert cap.endswith("Gmail")
    page = sanitize_browser_page({"hostname":"docs.google.com","title":title})
    assert "[truncated" in page["title"]
    assert len(page["title"]) <= 240
    assert cap_title_text("Short invoice - Gmail", max_chars=240) == "Short invoice - Gmail"
    # Never keep a partial unbroken opaque identifier at either edge.
    raw = "PR #207 | " + "Q"*200000 + " | Chrome"
    assert "Q"*25 not in cap_title_text(raw, max_chars=240)


def test_browser_privacy_tradeoff_does_not_change_default_capture():
    policy = public_settings()
    tradeoff = policy["privacy_tradeoff"]
    assert tradeoff["business_references_off_by_default"] is True
    assert tradeoff["conversation_linking_requires_business_references"] is True
    assert tradeoff["do_not_auto_enable_reference_capture"] is True
    assert policy["defaults"]["business_object_references"] is False
