"""Cross-branch acceptance for real proposal -> stage -> install -> roster.

No model or business system invoked. Uses the real structural reviewer and
renderer, not synthetic stand-ins for the manifest or package digest.
"""
from shared import ai_skill_stage as stage
from shared import ai_skill_project_install as install
from shared import native_shadow_roster as roster
from test_ai_implementation_review import source, proposal
from test_ai_skill_stage import fixture as draft_fixture
from test_native_shadow_roster import cases, run, snapshot


def test_rendered_package_and_frozen_roster_use_identical_lineage(tmp_path):
    sources = [source(True)]
    p = proposal(sources)
    _, _, spec = draft_fixture()
    spec['option_index'] = 1  # local_model in the actual proposal fixture
    spec['tool_mappings'] = {}
    files, manifest = stage.prepare(sources, p, spec)
    pin = manifest['package_sha256']
    package = tmp_path / 'package'
    assert stage.stage(package, sources, p, spec, pin, True) == manifest
    assert stage.verify(package, pin)['exact_staged_files_verified']
    plan = roster.freeze(manifest, pin, cases(2), confirmed=True)
    for k in ('package_sha256', 'packet_sha256', 'proposal_sha256', 'draft_sha256'):
        assert plan[k] == manifest[k]
    project = tmp_path / 'isolated-project'
    project.mkdir()
    receipt = install.operate('install', package, pin, project,
                              confirm_isolated=True, confirm_change=True)
    assert (project / receipt['skill_relative_path']).read_bytes() == files['claude/follow-up/SKILL.md']
    assert not receipt['skill_invocation_verified']
    assignments = roster.worklist(plan, plan['plan_sha256'])
    trace = snapshot(run(1))
    assignments[0]['root_execution_id'] = run(1)['execution_id']
    result = roster.review(plan, plan['plan_sha256'], trace, roster.digest(trace), assignments)
    assert result['case_count'] == 2
    assert result['counts']['not_assigned'] == 1
    assert not result['business_outcomes_verified'] and not result['execution_authorized']
    assert install.operate('remove', package, pin, project,
                           confirm_isolated=True, confirm_change=True)['skill_present_after_operation'] is False
