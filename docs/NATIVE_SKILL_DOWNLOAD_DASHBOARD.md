# Download reviewed native AI shadow skills from the dashboard (P0 #1663)

This feature connects the existing portable external-AI proposal review
(#1667) and shadow skill renderer (#1670) to the local automation pipeline.
It adds no inference engine, skill author, deployment system or opportunity
store.

## Direct handoff from the implementation portfolio

In the existing evidence preparation and opportunity panel, review either an
imported external AI proposal or a proposal generated using the authorized
loopback model. After **Review exact proposal** passes, select **Start new
shadow skill draft from reviewed proposal**. This explicitly replaces any
unfinished skill draft, opens the native skill panel and copies the original
source JSON strings in their exact order plus the exact reviewed proposal.
There is no intermediate file download or re-upload. Nothing is written to
the portfolio merely by starting the draft. The new draft is blank and any
prior package preview, approval checkbox and download eligibility are cleared.
Complete the human-reviewed draft, preview both files and confirm separately.
Choosing new original evidence files clears the transferred proposal; manual
proposal selection and the standalone file-based path remain available. No
approval or execution authorization is transferred, and the server reruns the
same structural review and renderer from the exact selected inputs.

## Manual file-based workflow

1. Prepare an evidence-linked external AI proposal using the existing
   portable packet workflow. Keep the original authorized evidence JSON
   files and returned proposal.
2. Open Evidence / Automation pipeline and expand **Prepare reviewed native
   AI shadow skills**. Select the same source files in the original order
   (use the explicit reorder controls) and the original proposal.
3. Complete the editable human-reviewed JSON draft, or load a previously
   prepared draft. The template includes the skill name, single-line
   description, zero-based exact proposed execution-option index,
   procedure, all selected required tool mappings, independent source
   verification, held-out tests, escalation, rollback and review note.
   See AI_NATIVE_SHADOW_SKILL.md for the draft format and example.
4. Choose **Preview reviewed shadow skill**. The local server reruns the
   existing structural proposal review and shadow skill renderer. Inspect
   both complete files, including all instructions and tool mappings.
   Keep the package digest independently. Any input edit or source reorder
   invalidates the displayed preview and confirmation.
5. Explicitly confirm isolated shadow use and download the ZIP. The
   server recomputes the original review and package and requires the
   same package digest. The browser also compares the downloaded archive
   bytes with the server's archive SHA-256 header. This is consistency
   checking, not an external signature.

The archive contains only claude/<skill>/SKILL.md,
CHATGPT_INSTRUCTIONS.md and manifest.json. Its canonical manifest and
file bytes are identical to the existing staging format. The ZIP is
deterministic with a fixed timestamp, stored entries and explicit regular
file modes. Verify after extracting into a new local directory:

```sh
python -m shared.ai_skill_stage verify --directory extracted-skill \
  --package-sha256 EXACT_PACKAGE_PIN
```

The existing native staging guide explains manual setup, independently
restricted client permissions and rollback. The archive is not an
installation, native ChatGPT deployment API or verified invocation.
Do not install into production; use an isolated test project with actual
tool restrictions. The authored instructions are not permission enforcement.

Both data endpoints require a local dashboard session, not the ordinary
API/MCP bearer. Original JSON strings are strictly parsed server-side;
duplicate keys are refused. The request is bounded to 12 MiB including
the JSON envelope. The browser additionally limits source/proposal files
to 8 MiB total and the draft to 256 KiB. No new evidence, draft or
package records are persisted on the server by this feature. Authenticated
successful responses are no-store. Nothing is sent to a model. Human-authored
instructions may contain sensitive material; review before sharing. Neither
a matching digest nor a downloaded archive verifies actual skill behavior,
business outcomes, source authenticity, client permissions or authorization.

## Validation

```sh
python -m pytest -q tests/test_ai_skill_archive.py tests/test_native_skill_download_routes.py
```

Focused archive tests use a controlled renderer callback. The integration
test uses the actual parent proposal and skill renderer and the real
FastAPI router, with session validation substituted. The existing Chromium
automation pipeline suite exercises preview, stale-input invalidation,
explicit confirmation, archive download and verification using actual
parent code. Full combined-tree CI and installed client acceptance remain
necessary.
