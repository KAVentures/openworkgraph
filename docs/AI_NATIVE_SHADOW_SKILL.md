# Native-client shadow skill staging (P0 #1663)

This feature complements the existing external-AI implementation proposal
review (#1667), not a new inference model, skill author or execution runtime.

The external AI authors the procedure; the company reviews and edits it.
Keep the original authorized evidence JSON files in the original packet order
and the exact returned proposal. Prepare a separate human-reviewed draft,
for example:

```json
{
  "name": "customer-follow-up",
  "description": "Draft customer follow-up for isolated evaluation",
  "option_index": 0,
  "instructions": "Draft a reply from synthetic cases only.",
  "tool_mappings": {"lookup": "Read-only fixture lookup"},
  "source_verification": "Independently compare the expected fixture status.",
  "test_plan": "Run frozen normal and exception cases.",
  "escalation": "Stop on missing records or unclear approvals.",
  "rollback": "Remove the staged skill, end the test session and discard drafts.",
  "review_note": "Reviewed against the exact proposal and permitted test scope."
}
```

The option index is zero-based; mapping keys must match its exact required
tool list. Use an empty mapping only if no tools are declared. Mapping
values describe customer-reviewed test equivalents, not access grants.
The CLI checks structure, not the truth or adequacy of the instructions.

## Preview, stage and verify

```sh
python -m shared.ai_skill_stage preview --source workflow.json \
  --proposal proposal.json --draft draft.json
```

Repeat --source for the original ordered evidence files. Review both
rendered texts and keep the printed package SHA-256 independently.
The preview contains the complete reviewed instructions and may be
sensitive. It does not write or install anything.

```sh
python -m shared.ai_skill_stage stage --source workflow.json \
  --proposal proposal.json --draft draft.json --directory new-shadow-package \
  --package-sha256 EXACT_PACKAGE_PIN --confirm-shadow-package

python -m shared.ai_skill_stage verify --directory new-shadow-package \
  --package-sha256 EXACT_PACKAGE_PIN
```

Stage recomputes the original structural review and exact rendered file
inventory, refusing a changed preview. It creates a new local directory;
the manifest is written last. Discard incomplete outputs if a write fails.
Verify checks the exact two instruction files and pinned manifest, refuses
unexpected files/directories and symlinks, and never invokes an AI.

The Claude Code artifact is claude/<name>/SKILL.md, using the documented
manual invocation setting disable-model-invocation: true. It does not
include allowed-tools, hooks, scripts or executable helpers, and the
known inline/fenced shell context injection forms are refused.
Reference: https://code.claude.com/docs/en/skills .
CHATGPT_INSTRUCTIONS.md is plain text for manual use in an authorized
test chat; it is not a native ChatGPT deployment API.

After independently restricting actual client tools and source access,
an authorized operator may copy the skill directory into an isolated test
project's .claude/skills/ and manually invoke it there. Verify the selected
client version, discovered skill, effective permissions and actual tool
calls; keep results for the same held-out cases. Do not copy into production
or rely on the prompt to enforce a sandbox. The staging utility neither
performs that setup nor verifies a live invocation. No model is called.

Rollback is customer-controlled: stop the test session, remove the copied
skill from the test environment and discard its draft outputs according
to the reviewed rollback plan. Removing a skill does not undo external
side effects, which is why the test environment must independently prevent
them. No package implies deployment, source authenticity, semantic
correctness, approval or execution authorization. The originals remain
necessary to reproduce the structural review. Do not include secrets or
unapproved evidence in the authored procedure.

## Tests

```sh
python -m pytest -q tests/test_ai_skill_stage.py tests/test_ai_skill_stage_integration.py
```

Core tests use controlled reviewer callbacks. The integration test uses
the actual #1667 portable proposal reviewer. Neither replaces installed
client acceptance or real shadow outcome verification.
