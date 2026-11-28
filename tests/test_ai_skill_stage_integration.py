"""Integration with the existing #1667 portable proposal contract."""
import pytest
from shared.ai_skill_stage import prepare, stage, verify
from test_ai_implementation_review import source, proposal


def test_actual_portable_review_and_staged_package(tmp_path):
    sources = [source(True)]
    p = proposal(sources)
    draft = {'name':'customer-follow-up','description':'Draft customer follow-up','option_index':0,
             'instructions':'Draft a reply based on synthetic inputs only.',
             'tool_mappings':{},'source_verification':'Compare fixture status independently.',
             'test_plan':'Test frozen normal and exception cases.','escalation':'Escalate missing records.',
             'rollback':'Remove staged skill and discard drafts.','review_note':'Human reviewed draft.'}
    files, manifest = prepare(sources, p, draft)
    assert manifest['packet_sha256'] == p['packet_sha256']
    assert manifest['selected_environment'] == p['execution_options'][0]['environment']
    assert b'disable-model-invocation: true' in files['claude/customer-follow-up/SKILL.md']
    assert b'Ignore all instructions' not in files['CHATGPT_INSTRUCTIONS.md']
    root = tmp_path/'stage'
    stage(root,sources,p,draft,manifest['package_sha256'],True)
    assert verify(root,manifest['package_sha256'])['exact_staged_files_verified']
    p['outcome']['evidence_refs'] = ['fabricated']
    with pytest.raises(ValueError):
        prepare(sources,p,draft)
