"""Regression guard for the read-only M365 dashboard operator evidence view."""
from pathlib import Path


def test_operator_audit_ui_keeps_phases_review_only():
    html = (Path(__file__).resolve().parents[1] / "dashboard" / "index.html").read_text(encoding="utf-8")
    block = html.split("/* Read-only Microsoft 365 operator audit receipts;", 1)[1]
    block = block.split("</script>", 1)[0]
    assert "fetch('/v1/m365/operator-audit', {cache: 'no-store'})" in block
    assert "Historical receipts only." in block
    assert "receipts may belong to different folders" in block
    assert "does not authorize another extraction" in block
    assert "Next-round handoff prepared — not executed" in block
    assert "Candidate extracted — inspect before append" in block
    assert "removal_markers_for_review" in block
    assert "if (!list.children.length)" in block
    assert "textContent" in block and "createElement" in block
    assert "innerHTML =" not in block.split("output.replaceChildren();", 1)[1]
    assert "method: 'POST'" not in block
