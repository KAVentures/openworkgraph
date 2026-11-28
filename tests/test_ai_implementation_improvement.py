import copy
import pytest
from shared import ai_implementation_improvement as improvement
from shared import ai_implementation_review as contract
from test_ai_implementation_forecast_review import fixture, compare


def inputs():
    f = fixture(); args = f[0]; report = compare(f)
    return args[0], args[2][0], report, report['forecast_review_sha256']


def test_minimized_packet_preserves_gates_and_pins():
    args = inputs(); p = improvement.prepare(*args)
    assert p['original_proposal'] == args[1]
    assert p['feedback']['comparisons'] == args[2]['comparisons']
    assert p['feedback']['review_gates'] == args[2]['review_gates']
    assert not p['feedback']['all_declared_gates_met']
    assert p['feedback']['excluded_measurement_count'] == 1
    assert 'idea_id' not in p and 'selected_measurement_ids' not in p['feedback']
    assert 'basis_note' not in p['feedback']['comparability']
    assert 'meas_' not in contract._json(p).decode()
    assert not p['external_sharing_authorized'] and not p['execution_authorized']
    assert p['feedback_sha256'] == contract._digest({k:v for k,v in p.items() if k!='feedback_sha256'})


def test_review_revision_exact_and_unverified():
    args = inputs(); p = improvement.prepare(*args); revised = copy.deepcopy(args[1])
    revised['steps'][0]['text'] = 'Proposed smaller model with human review'
    revised['steps'][0]['basis'] = 'proposed'
    revised['questions'].append('What is the quality threshold?')
    r = improvement.revise(*args, p['feedback_sha256'], revised)
    assert r['changed_fields'] == ['steps','questions']
    assert r['revised_proposal_sha256'] == contract._digest(revised)
    assert not r['improvement_verified'] and not r['workflow_replaced'] and not r['execution_authorized']


@pytest.mark.parametrize('bad',['forecast','original','feedback','unchanged','title_only','citation','new_packet','permission'])
def test_refuses_stale_or_invalid(bad):
    args=list(inputs()); p=improvement.prepare(*args); pin=p['feedback_sha256']; revised=copy.deepcopy(args[1])
    revised['steps'][0]['text']='Different candidate'
    if bad=='forecast': args[2]['all_declared_gates_met']=True
    elif bad=='original': args[1]['title']='Changed title'
    elif bad=='feedback': pin='0'*64
    elif bad=='unchanged': revised=copy.deepcopy(args[1])
    elif bad=='title_only': revised=copy.deepcopy(args[1]);revised['title']='New name'
    elif bad=='citation': revised['steps'][0]['evidence_refs']=['invented']
    elif bad=='new_packet': revised['packet_sha256']='0'*64
    elif bad=='permission': revised['execution_authorized']=True
    with pytest.raises(ValueError): improvement.revise(*args,pin,revised)


def test_size_limit(monkeypatch):
    monkeypatch.setattr(improvement,'MAX_BYTES',100)
    with pytest.raises(ValueError): improvement.prepare(*inputs())
