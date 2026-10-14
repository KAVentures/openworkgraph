"""Guard the public ZIP download contract across release automation changes.

GitHub and the website intentionally use /releases/latest/download/<asset>.
Publishing an immutable validated main build as latest keeps both destinations
current without rewriting either link on every merged PR.
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RELEASE = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
LATEST = "https://github.com/KAVentures/openworkgraph/releases/latest/download/"


def test_github_front_page_uses_latest_release_zip_assets():
    for name in ("OpenWorkGraph-macOS.zip", "OpenWorkGraph-Windows.zip"):
        assert LATEST + name in README


def test_verified_main_builds_publish_even_without_a_version_bump():
    assert 'TAG="build-v${VERSION}-${GITHUB_SHA:0:12}"' in RELEASE
    assert 'if gh release view "$STABLE_TAG"' in RELEASE
    assert '"$GITHUB_SHA"' in RELEASE
    assert "--latest " in RELEASE
    # The old branch that skipped every same-version release must not return.
    assert "Bump VERSION to publish a new GitHub Release." not in RELEASE
    assert "gh release upload" not in RELEASE


def test_failed_tests_or_superseded_main_cannot_promote_downloads():
    assert "actions: read" in RELEASE
    assert "actions/workflows/tests.yml/runs?event=push&head_sha=$GITHUB_SHA" in RELEASE
    assert "completed:success)" in RELEASE
    assert "publish=false" in RELEASE
    assert "if: steps.publish-gate.outputs.publish == 'true'" in RELEASE
    assert 'gh api "repos/$GITHUB_REPOSITORY/branches/main"' in RELEASE
    assert "cancel-in-progress: false" in RELEASE


def test_latest_downloads_are_checked_after_publication():
    assert "Verify GitHub latest ZIPs match the built artifacts" in RELEASE
    assert "/releases/latest/download/$name" in RELEASE
    assert 'cmp "/tmp/$name" "release-assets/$name"' in RELEASE
    assert 'unzip -tq "/tmp/$name"' in RELEASE
