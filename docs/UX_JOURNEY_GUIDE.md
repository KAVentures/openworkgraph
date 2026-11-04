# Guided local work journey

The dashboard now exposes five explicit, keyboard-operable navigation steps above its existing workspaces: opt-in settings, local event-log discovery, saved process map/editor, human opportunity review, and agent connection settings.

Each step resolves a **real, currently mounted** target before changing the active tab. When a target is absent, the guide refuses navigation and announces the missing workspace in a live status region. On success it activates the existing tab and focuses the actual target. The guide does not store completion state or fabricate consent, workflow approval, handoff authorization, provider state or verified outcomes.

## Operator walkthrough

1. Review opt-in and retention controls in Settings. No toggle is changed by the guide.
2. Inspect the event-log workspace in Evidence. An absent workspace is reported, not silently substituted.
3. Inspect the saved process catalog/editor. Map contents remain reviewable and must be validated separately.
4. Open the automation pipeline, select an opportunity, and explicitly inspect its evidence journey and review decisions. Approval there is **not** execution authority.
5. Open Connect AI to inspect connection options. A real agent offer still requires its own authenticated, explicitly authorized workflow. This navigation does not send an offer or execute an agent.

## Acceptance and limitations

The Node-backed regression in `tests/test_ux_journey_guide.py` executes the production inline JavaScript with a simulated DOM and verifies all five tab transitions, focus movement, missing-target refusal and informational status. This is **not** a full Chromium/Playwright or consenting-user acceptance test; required repository browser/native CI and real pilot gates remain separate. No provider calls, writes, capture consent changes, or execution requests are initiated by the guide.

This is a navigation vertical slice, not the complete opt-in-to-authorized-agent pilot. The remaining work is an exact-revision review-to-owner-offer integration with explicit grant checks, stale/revoked refusal, and independent consenting-user evidence.
