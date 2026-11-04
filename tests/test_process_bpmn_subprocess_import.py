"""Plain embedded BPMN subprocesses are inlined exactly; everything else is refused."""
import pytest

from shared.process_bpmn_import import BPMN, BPMNImportError, import_bpmn


def xml(content):
    return f'<definitions xmlns="{BPMN}"><process id="p">{content}</process></definitions>'.encode()


def sub(body, attrs='', name='Credit check'):
    return f'<subProcess id="sp" name="{name}" {attrs}>{body}</subProcess>'


PLAIN_BODY = '''<startEvent id="ss"/><task id="t1" name="Pull report"/><task id="t2" name="Score"/>
  <endEvent id="se"/><sequenceFlow id="i1" sourceRef="ss" targetRef="t1"/>
  <sequenceFlow id="i2" sourceRef="t1" targetRef="t2"/><sequenceFlow id="i3" sourceRef="t2" targetRef="se"/>'''


def around(inner):
    return xml(f'''<startEvent id="s" name="Start"/><task id="a" name="Receive"/>{inner}
      <task id="z" name="Decide"/><endEvent id="e" name="End"/>
      <sequenceFlow id="f1" sourceRef="s" targetRef="a"/><sequenceFlow id="f2" sourceRef="a" targetRef="sp"/>
      <sequenceFlow id="f3" sourceRef="sp" targetRef="z"/><sequenceFlow id="f4" sourceRef="z" targetRef="e"/>''')


def _path(result):
    model = result['process_model']
    labels = {a['id']: a['label'] for a in model['activities']}
    nxt = {t['source']: t['target'] for t in model['transitions']}
    node, path = model['entry_activity_id'], []
    while node:
        path.append(labels[node]); node = nxt.get(node)
    return path


def test_plain_subprocess_is_inlined_in_order_and_grouping_is_reported():
    result = import_bpmn(around(sub(PLAIN_BODY)))
    assert _path(result) == ['Start', 'Receive', 'Pull report', 'Score', 'Decide', 'End']
    assert result['inlined_subprocesses'][0]['label'] == 'Credit check'
    groups = result['inlined_subprocesses'][0]['activity_ids']
    labels = {a['id']: a['label'] for a in result['process_model']['activities']}
    assert [labels[i] for i in groups] == ['Pull report', 'Score']
    assert 'embedded_subprocesses_inlined_grouping_not_saved_in_model' in result['warnings']
    assert 'sp' not in result['source_node_ids'] and 'ss' not in result['source_node_ids']


def test_nested_subprocess_and_inner_exclusive_choice_are_preserved():
    inner = '''<subProcess id="nsp" name="Verify"><startEvent id="ns"/><task id="v" name="Verify ID"/><endEvent id="ne"/>
      <sequenceFlow id="n1" sourceRef="ns" targetRef="v"/><sequenceFlow id="n2" sourceRef="v" targetRef="ne"/></subProcess>'''
    body = f'''<startEvent id="ss"/>{inner}<exclusiveGateway id="g" name="Risky"/><task id="h" name="Manual review"/>
      <task id="l" name="Auto approve"/><exclusiveGateway id="m" name="Join"/><endEvent id="se"/>
      <sequenceFlow id="i1" sourceRef="ss" targetRef="nsp"/><sequenceFlow id="i2" sourceRef="nsp" targetRef="g"/>
      <sequenceFlow id="i3" sourceRef="g" targetRef="h"><conditionExpression>risk == high</conditionExpression></sequenceFlow>
      <sequenceFlow id="i4" sourceRef="g" targetRef="l"/><sequenceFlow id="i5" sourceRef="h" targetRef="m"/>
      <sequenceFlow id="i6" sourceRef="l" targetRef="m"/><sequenceFlow id="i7" sourceRef="m" targetRef="se"/>'''
    result = import_bpmn(around(sub(body)))
    model = result['process_model']
    labels = {a['id']: a['label'] for a in model['activities']}
    assert {'Verify ID', 'Risky', 'Manual review', 'Auto approve', 'Join'} <= set(labels.values())
    assert len(model['business_rules']) == 2 and not any(r['user_confirmed'] for r in model['business_rules'])
    outer, nested = sorted(result['inlined_subprocesses'], key=lambda g: -len(g['activity_ids']))
    assert nested['label'] == 'Verify' and [labels[i] for i in nested['activity_ids']] == ['Verify ID']
    assert set(nested['activity_ids']) < set(outer['activity_ids'])


def test_subprocess_documentation_becomes_a_review_note():
    body = '<documentation>Only for new customers</documentation>' + PLAIN_BODY
    result = import_bpmn(around(sub(body)))
    assert any('Only for new customers' in n['text'] for n in result['documentation_notes'])
    assert result['retained_manual_review_condition_count'] >= 1


@pytest.mark.parametrize('inner', [
    sub(PLAIN_BODY, 'triggeredByEvent="true"'),
    sub(PLAIN_BODY.replace('<startEvent id="ss"/>', '<startEvent id="ss"/><standardLoopCharacteristics/>')),
    sub(PLAIN_BODY + '<multiInstanceLoopCharacteristics/>'),
    sub(PLAIN_BODY + '<boundaryEvent id="b" attachedToRef="t1"/>'),
    sub(PLAIN_BODY.replace('<startEvent id="ss"/>', '<startEvent id="ss"><timerEventDefinition/></startEvent>')),
    sub(PLAIN_BODY.replace('<endEvent id="se"/>', '<endEvent id="se"/><endEvent id="se2"/>')),
    sub('<startEvent id="ss"/><endEvent id="se"/><sequenceFlow id="i1" sourceRef="ss" targetRef="se"/>'),
    sub(PLAIN_BODY.replace('<sequenceFlow id="i1" sourceRef="ss" targetRef="t1"/>',
                           '<sequenceFlow id="i1" sourceRef="ss" targetRef="t1" name="only if"/>')),
    '<transaction id="sp">' + PLAIN_BODY + '</transaction>',
    '<callActivity id="sp" calledElement="other"/>',
])
def test_subprocess_semantics_that_inlining_cannot_preserve_are_refused(inner):
    with pytest.raises(BPMNImportError):
        import_bpmn(around(inner))


def test_conditional_exit_or_extra_entries_are_refused():
    raw = around(sub(PLAIN_BODY)).replace(b'<sequenceFlow id="f3" sourceRef="sp" targetRef="z"/>',
        b'<sequenceFlow id="f3" sourceRef="sp" targetRef="z"><conditionExpression>x</conditionExpression></sequenceFlow>')
    with pytest.raises(BPMNImportError):
        import_bpmn(raw)
    raw = around(sub(PLAIN_BODY)).replace(b'<sequenceFlow id="f4"',
        b'<sequenceFlow id="f9" sourceRef="s" targetRef="sp"/><sequenceFlow id="f4"')
    with pytest.raises(BPMNImportError):
        import_bpmn(raw)


def test_nesting_depth_is_bounded():
    body = PLAIN_BODY
    for level in range(4):
        body = (f'<startEvent id="s{level}"/><subProcess id="x{level}">{body}</subProcess><endEvent id="e{level}"/>'
                f'<sequenceFlow id="a{level}" sourceRef="s{level}" targetRef="x{level}"/>'
                f'<sequenceFlow id="b{level}" sourceRef="x{level}" targetRef="e{level}"/>')
    with pytest.raises(BPMNImportError):
        import_bpmn(around(sub(body)))
