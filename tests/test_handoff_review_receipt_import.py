"""R5 local receipt import contract: executable browser-source safety checks."""
from pathlib import Path

HTML = Path(__file__).resolve().parents[1] / "dashboard" / "handoff_review_workspace.html"


def test_receipt_import_requires_exact_packet_digest_and_identity():
    html = HTML.read_text()
    assert 'id="import-decisions"' in html
    assert "receipt.baseline_packet_sha256!==current.digest" in html
    for check in (
        "receipt.idea_id!==current.packet.idea.idea_id",
        "receipt.idea_revision!==current.packet.idea.revision",
        "receipt.project_id!==current.packet.project.project_id",
        "receipt.project_spec_sha256!==current.packet.project.spec_sha256",
        "receipt.decisions.length!==current.blockers.length",
        "d.blocker!==current.blockers[i]",
    ):
        assert check in html


def test_receipt_import_rejects_authority_and_stale_async_results():
    html = HTML.read_text()
    for check in (
        "receipt.execution_authorized!==false",
        "receipt.source_read_authorized!==false",
        "receipt.consent_verified!==false",
        "receipt.owner_approval_verified!==false",
        "receipt.server_freshness_verified!==false",
        "generation!==reviewGeneration||activeReview!==current",
        "Receipt import refused:",
    ):
        assert check in html
    assert "fetch(" not in html
    assert "localStorage" not in html
