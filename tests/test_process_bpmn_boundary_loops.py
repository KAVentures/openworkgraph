"""BPMN boundary events, task loops and call activities lowered to exact equivalents."""
import pytest

from shared.process_bpmn_import import BPMN, BPMNImportError, import_bpmn
from shared.process_conformance import compare_path
from shared.process_model import ProcessModel


def xml(content):
    return f'<definitions xmlns="{BPMN}"><process id="p">{content}</process></definitions>'.encode()


def around(task):
    return xml(f'''<startEvent id="s" name="Start"/>{task}<task id="z" name="Archive"/><endEvent id="e" name="End"/>
      <sequenceFlow id="f1" sourceRef="s" targetRef="t"/><sequenceFlow id="f2" sourceRef="t" targetRef="z"/>
      <sequenceFlow id="f3" sourceRef="z" targetRef="e"/>''')


def _model(result):
    return ProcessModel.model_validate(result["process_model"])


def test_interrupting_error_and_timer_boundaries_become_an_outcome_choice():
    raw = xml('''<startEvent id="s" name="Start"/><task id="t" name="Charge card"/>
      <boundaryEvent id="b1" name="Declined" attachedToRef="t"><errorEventDefinition errorRef="CardDeclined"/></boundaryEvent>
      <boundaryEvent id="b2" attachedToRef="t"><timerEventDefinition><timeDuration>PT2H</timeDuration></timerEventDefinition></boundaryEvent>
      <task id="ok" name="Ship order"/><task id="fail" name="Ask for new card"/><task id="late" name="Escalate"/>
      <endEvent id="e" name="End"/>
      <sequenceFlow id="f1" sourceRef="s" targetRef="t"/><sequenceFlow id="f2" sourceRef="t" targetRef="ok"/>
      <sequenceFlow id="f3" sourceRef="b1" targetRef="fail"/><sequenceFlow id="f4" sourceRef="b2" targetRef="late"/>
      <sequenceFlow id="f5" sourceRef="ok" targetRef="e"/><sequenceFlow id="f6" sourceRef="fail" targetRef="e"/>
      <sequenceFlow id="f7" sourceRef="late" targetRef="e"/>''')
    result = import_bpmn(raw)
    model = _model(result)
    rules = sorted(r.text for r in model.business_rules)
    assert rules == ["Charge card completed normally", "Error Declined (CardDeclined) raised by Charge card",
                     "Timer (PT2H) elapsed during Charge card"]
    assert not any(r.user_confirmed for r in model.business_rules)
    assert "interrupting_boundary_events_imported_as_exclusive_outcome_choices" in result["warnings"]
    for path in (("Charge card", "Ship order"), ("Charge card", "Ask for new card"), ("Charge card", "Escalate")):
        assert compare_path(path, model)["exact_structural_match"] is True
    assert compare_path(("Charge card", "Ship order", "Escalate"), model)["exact_structural_match"] is False


@pytest.mark.parametrize("loop,kind", [
    ('<standardLoopCharacteristics><loopCondition>items_left</loopCondition></standardLoopCharacteristics>', "standard_loop"),
    ('<multiInstanceLoopCharacteristics isSequential="true"><loopCardinality>3</loopCardinality></multiInstanceLoopCharacteristics>',
     "sequential_multi_instance"),
])
def test_sequential_loops_become_an_exclusive_repeat(loop, kind):
    result = import_bpmn(around(f'<task id="t" name="Pick item">{loop}</task>'))
    model = _model(result)
    assert result["lowered_constructs"] == [{"kind": kind}]
    assert any("is needed" in r.text for r in model.business_rules)
    for repeats in (1, 2, 4):
        assert compare_path(("Pick item",) * repeats + ("Archive",), model)["exact_structural_match"] is True
    assert compare_path(("Archive",), model)["exact_structural_match"] is False


def test_call_activity_is_one_step_with_a_review_note():
    result = import_bpmn(around('<callActivity id="t" name="Run credit check" calledElement="CreditCheck"/>'))
    model = _model(result)
    assert compare_path(("Run credit check", "Archive"), model)["exact_structural_match"] is True
    assert any("Calls process CreditCheck" in n["text"] for n in result["documentation_notes"])
    assert "call_activities_imported_as_single_steps_called_process_not_expanded" in result["warnings"]
    assert result["retained_manual_review_condition_count"] >= 1


@pytest.mark.parametrize("task", [
    '<task id="t" name="Pick"><multiInstanceLoopCharacteristics/></task>',
    '<task id="t" name="Pick"><standardLoopCharacteristics testBefore="true"/></task>',
    '<task id="t" name="Pick"/><boundaryEvent id="b" attachedToRef="t" cancelActivity="false"><timerEventDefinition/></boundaryEvent>'
    '<task id="h" name="Remind"/><sequenceFlow id="fb" sourceRef="b" targetRef="h"/><sequenceFlow id="fh" sourceRef="h" targetRef="z"/>',
    '<task id="t" name="Pick"/><boundaryEvent id="b" attachedToRef="t"><messageEventDefinition/></boundaryEvent>'
    '<task id="h" name="Reply"/><sequenceFlow id="fb" sourceRef="b" targetRef="h"/><sequenceFlow id="fh" sourceRef="h" targetRef="z"/>',
    '<task id="t" name="Pick"/><boundaryEvent id="b" attachedToRef="missing"><errorEventDefinition/></boundaryEvent>',
    '<callActivity id="t" name="Sub" calledElement="X"><extensionElements/></callActivity>',
])
def test_semantics_that_would_change_are_refused(task):
    with pytest.raises(BPMNImportError):
        import_bpmn(around(task))


def test_generated_ids_never_collide_with_source_ids():
    raw = around('<task id="t" name="Pick"><standardLoopCharacteristics/></task><task id="t_loop__owg" name="Other"/>'
                 '<sequenceFlow id="fo1" sourceRef="z" targetRef="t_loop__owg"/>').replace(
        b'<sequenceFlow id="f3" sourceRef="z" targetRef="e"/>', b'<sequenceFlow id="f3" sourceRef="t_loop__owg" targetRef="e"/>')
    result = import_bpmn(raw)
    labels = {a.label for a in _model(result).activities}
    assert {"Pick", "Other", "Archive"} <= labels
