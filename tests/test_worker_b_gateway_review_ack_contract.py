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
    assert end < source.index("render(reviewResult)", start)


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
