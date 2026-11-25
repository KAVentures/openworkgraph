"""Paired communications comparison: same examples, explicit abstention and no text."""
from copy import deepcopy

import pytest

from shared import communications_mining as mining
from shared import communications_learning as learning
from shared import communications_paired_evaluation as paired

NAMES=["Billing","Refund","Other"]
TAXONOMY={"format":"openworkgraph.communications-taxonomy.v1","intents":[
    {"name":"Billing","terms":["invoice"]},{"name":"Refund","terms":["refund"]},
    {"name":"Other","terms":["miscellaneous"]}]}


def example(i,text,labels,language="en"):
    return {"example_id":str(i),"text":text,"labels":labels,"language":language}


def evaluate(monkeypatch,holdout,training=(),predictions=None):
    fake={"intents":{"Billing":{},"Refund":{}},"thresholds":{"present_at":0.7,"absent_at":0.3}}
    predictions=predictions or {}
    monkeypatch.setattr(learning,"predictor",lambda _model:lambda text:predictions.get(text,{"Billing":0.1,"Refund":0.1}))
    return paired.evaluate(fake,list(training),holdout,TAXONOMY,NAMES)


def test_keyword_ties_and_no_match_are_distinguished():
    parsed=mining.parse_taxonomy(TAXONOMY)
    assert paired.keyword_decisions("invoice refund",parsed,NAMES)=={
        "Billing":"uncertain","Refund":"uncertain","Other":"absent"}
    assert paired.keyword_decisions("invoice",parsed,NAMES)=={
        "Billing":"present","Refund":"absent","Other":"absent"}
    assert set(paired.keyword_decisions("nothing",parsed,NAMES).values())=={"absent"}


def test_paired_accuracy_recall_coverage_and_unknown_intents(monkeypatch):
    messages=[example(1,"invoice",["Billing"]),
              example(2,"invoice refund",["Billing","Refund"]),
              example(3,"please reimburse",["Refund"],"sv"),
              example(4,"nothing",[])]
    predictions={"invoice":{"Billing":0.9,"Refund":0.1},
                 "invoice refund":{"Billing":0.9,"Refund":0.9},
                 "please reimburse":{"Billing":0.1,"Refund":0.9},
                 "nothing":{"Billing":0.1,"Refund":0.5}}
    result=evaluate(monkeypatch,messages,predictions=predictions)
    all_=result["all_holdout"]
    by={r["intent"]:r for r in all_["per_intent"]}
    refund=by["Refund"]
    assert refund["keyword"]["abstained_positive"]==1
    assert refund["keyword"]["fn"]==1 and refund["keyword"]["recall_all_positives"]==0
    assert refund["learned"]["tp"]==2 and refund["learned"]["abstained_negative"]==1
    assert refund["learned"]["coverage"]==0.75
    assert refund["learned"]["accuracy_all"]==0.75 and refund["learned"]["accuracy_decided"]==1
    assert refund["paired"]["learned_only_correct"]==2
    assert refund["paired"]["keyword_only_correct"]==1
    assert by["Other"]["learned"]["coverage"]==0
    assert by["Other"]["learned"]["accuracy_decided"] is None
    assert all_["fully_decided_message_coverage"]["learned"]==0
    assert sum(t["count"] for t in all_["strict_all_intent_message_comparison"]["transitions"])==4
    assert result["untrained_intents"]==["Other"]
    assert {x["language"] for x in result["by_language"]}=={"en","sv"}
    assert not result["holdout_used_to_fit_or_select_thresholds"]
    assert not result["external_quality_verified"]
    assert "please reimburse" not in str(result)


def test_overlap_screening_preserves_full_result_and_counts(monkeypatch):
    training=[example(0,"invoice number 111 due today",["Billing"])]
    holdout=[example(1,"Invoice number 222 due today",["Billing"]),example(2,"refund",["Refund"])]
    result=evaluate(monkeypatch,holdout,training)
    assert result["all_holdout"]["message_count"]==2
    assert result["lexical_overlap"]["normalized_exact_overlap"]==1
    assert result["without_detected_lexical_overlap"]["message_count"]==1
    assert result["by_language"][0]["without_detected_lexical_overlap"]["message_count"]==1


def test_incomplete_overlap_suppresses_only_screened_evaluation(monkeypatch):
    monkeypatch.setattr(paired.overlap,"audit",lambda a,b:({"complete":False},[]))
    result=evaluate(monkeypatch,[example(1,"refund",["Refund"])])
    assert result["all_holdout"]["message_count"]==1
    assert result["without_detected_lexical_overlap"] is None
    assert result["by_language"][0]["without_detected_lexical_overlap"] is None


def test_empty_holdout_does_not_claim_accuracy(monkeypatch):
    result=evaluate(monkeypatch,[])
    assert result["all_holdout"]["micro"]["learned"]["accuracy_all"] is None
    assert result["all_holdout"]["strict_all_intent_message_comparison"]["unadjusted_exact_mcnemar_p"] is None


def test_retired_labels_and_duplicate_identifiers_refuse(monkeypatch):
    with pytest.raises(learning.LearningError,match="retired"):
        evaluate(monkeypatch,[example(1,"invoice",["Retired"])])
    with pytest.raises(learning.LearningError,match="distinct"):
        evaluate(monkeypatch,[example(1,"invoice",["Billing"])],[example(1,"refund",["Refund"])])


def test_exact_paired_test_known_cases():
    assert paired._mcnemar(0,0) is None
    assert paired._mcnemar(3,0)==0.25
    assert paired._mcnemar(2,2)==1
    assert paired._mcnemar(10,0)==pytest.approx(2/1024)
    assert paired._mcnemar(1000,1000)==1


def test_work_limit_refuses_rather_than_partial_counts(monkeypatch):
    monkeypatch.setattr(paired,"MAX_HOLDOUT_CHARACTERS",1)
    with pytest.raises(learning.LearningError,match="work bound"):
        evaluate(monkeypatch,[example(1,"refund",["Refund"])])
