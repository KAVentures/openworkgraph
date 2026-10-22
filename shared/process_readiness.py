"""Descriptive saved-design findings; no readiness score or execution approval."""
from .process_model import ProcessModel


def design_findings(value):
    model=ProcessModel.model_validate(value)
    work=[node for node in model.activities if node.kind in {'activity','decision'}]
    confirmed_terminals={expectation.terminal_activity_id for expectation in model.outcome_expectations
                         if expectation.user_confirmed}
    return {
        'unconfirmed_rule_ids':[rule.id for rule in model.business_rules if not rule.user_confirmed],
        'text_only_rule_ids':[rule.id for rule in model.business_rules if rule.predicate is None],
        'structured_rule_ids_requiring_source_fact_implementation':[
            rule.id for rule in model.business_rules if rule.predicate is not None],
        'unmapped_activity_ids':[node.id for node in model.activities
                                 if node.kind=='activity' and node.implementation=='unmapped'],
        'activity_ids_without_declared_responsibility':[node.id for node in work if not node.responsibility_label],
        'manual_review_activity_ids':sorted({condition.activity_id for condition in model.manual_review_conditions}),
        'terminal_ids_without_confirmed_source_expectation':[
            identifier for identifier in model.terminal_activity_ids if identifier not in confirmed_terminals],
    }
