"""Paired keyword/learned intent evaluation on an unchanged reviewed holdout.

No tuning on holdout, per-message results, example IDs or text in reports.
The keyword projection mirrors its single-label behavior; ties abstain for
tied winners. Untrained learned intents are unknown, not negative.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256
import json
import math

from . import communications_learning as learning
from . import communications_mining as mining
from . import communications_holdout_overlap as overlap
from . import communications_probability_reliability as reliability

FORMAT = "openworkgraph.communications-paired-evaluation.v1"
MAX_EXAMPLES = 2000
MAX_HOLDOUT_CHARACTERS = 2_000_000
MAX_INTENTS = 40
MAX_LANGUAGE_GROUPS = 30


def digest(value):
    return sha256(json.dumps(value,sort_keys=True,ensure_ascii=True,
                             separators=(",",":"),allow_nan=False).encode()).hexdigest()


def keyword_decisions(text, taxonomy, names):
    winner,scores=mining.classify(text,taxonomy)
    if not scores:
        return {name:"absent" for name in names}
    best=max(scores.values())
    tied={name for name,score in scores.items() if score==best}
    # Do not interpret all matched terms as positive labels. The existing
    # keyword product selects one winner or calls a tie ambiguous.
    return {name:("present" if name==winner and len(tied)==1 else
                  "uncertain" if name in tied and len(tied)>1 else "absent")
            for name in names}


def _ratio(a,b):
    return round(a/b,8) if b else None


def _mcnemar(b,c):
    """Two-sided exact conditional binomial test of paired discordances.

    Descriptive unadjusted p value; no independent sampling is assumed or
    inferred and no multiple-comparison significance verdict is produced.
    """
    n=b+c
    if n==0:
        return None
    # Integer binomial coefficients avoid overflow and underflow inside
    # the summation; float conversion happens only after exact division.
    numerator=2*sum(math.comb(n,k) for k in range(min(b,c)+1))
    return min(1.0,numerator/(1<<n))


def _decision_state(predicted,truth):
    if predicted not in ("present","absent"):
        return "abstained"
    return "correct" if (predicted=="present")==truth else "incorrect"


def _method(counts):
    tp,fp,tn,fn,up,un=(counts[k] for k in ("tp","fp","tn","fn","abstained_positive","abstained_negative"))
    total=tp+fp+tn+fn+up+un
    # Conservative recall includes positive abstentions; selective accuracy
    # is separately labeled and cannot conceal discarded examples.
    return {"examples":total,"tp":tp,"fp":fp,"tn":tn,"fn":fn,
            "abstained_positive":up,"abstained_negative":un,
            "coverage":_ratio(tp+fp+tn+fn,total),
            "accuracy_all":_ratio(tp+tn,total),
            "accuracy_decided":_ratio(tp+tn,tp+fp+tn+fn),
            "precision":_ratio(tp,tp+fp),"recall_all_positives":_ratio(tp,tp+fn+up),
            "f1_with_positive_abstentions":_ratio(2*tp,2*tp+fp+fn+up)}


def _increment(counts,decision,truth):
    if decision=="present":
        counts["tp" if truth else "fp"]+=1
    elif decision=="absent":
        counts["fn" if truth else "tn"]+=1
    else:
        counts["abstained_positive" if truth else "abstained_negative"]+=1


def _pair_summary(counts):
    states=("correct","incorrect","abstained")
    n=sum(counts.values())
    learned_only=sum(counts[(a,"correct")] for a in ("incorrect","abstained"))
    keyword_only=sum(counts[("correct",b)] for b in ("incorrect","abstained"))
    return {"examples":n,"transitions":[{"keyword":a,"learned":b,"count":counts[(a,b)]}
                                        for a in states for b in states],
            "learned_only_correct":learned_only,"keyword_only_correct":keyword_only,
            "paired_correctness_difference":_ratio(learned_only-keyword_only,n),
            "unadjusted_exact_mcnemar_p":_mcnemar(learned_only,keyword_only)}


def _summarize(rows,names):
    groups={}
    messages=Counter()
    full=Counter()
    for row in rows:
        truth,kw,lr,_probabilities=row
        kw_states=[];lr_states=[]
        for name in names:
            item=groups.setdefault(name,{"keyword":Counter(),"learned":Counter(),"paired":Counter()})
            y=name in truth
            a,b=kw[name],lr[name]
            _increment(item["keyword"],a,y);_increment(item["learned"],b,y)
            ka,la=_decision_state(a,y),_decision_state(b,y)
            item["paired"][(ka,la)]+=1
            kw_states.append(ka);lr_states.append(la)
        # All-intent correctness is strict: every declared intent must be
        # decided correctly, including negative labels. Abstention is not a
        # false label, but cannot qualify as an exact complete prediction.
        def message_state(states):
            if "incorrect" in states:return "incorrect"
            if "abstained" in states:return "abstained"
            return "correct"
        messages[(message_state(kw_states),message_state(lr_states))]+=1
        for label,states in (("keyword",kw_states),("learned",lr_states)):
            full[(label,"decided")]+=int("abstained" not in states)
    per_intent=[]
    micro={"keyword":Counter(),"learned":Counter()}
    for name in names:
        item=groups.get(name,{"keyword":Counter(),"learned":Counter(),"paired":Counter()})
        for method in micro:micro[method].update(item[method])
        per_intent.append({"intent":name,**{method:_method(item[method]) for method in micro},
                           "paired":_pair_summary(item["paired"])})
    return {"message_count":len(rows),"declared_intents":len(names),
            "per_intent":per_intent,
            "micro":{method:_method(counts) for method,counts in micro.items()},
            "strict_all_intent_message_comparison":_pair_summary(messages),
            "fully_decided_message_coverage":{method:_ratio(full[(method,"decided")],len(rows))
                                               for method in micro}}


def evaluate(model,training,holdout,taxonomy,names):
    """Paired evaluation on fixed reviewed examples; both methods share rows.

    The screened result excludes only detected lexical overlap. Reusing this
    report to tune the taxonomy/model would invalidate independent testing.
    No threshold is selected, no learned parameters are updated.
    """
    names=sorted(set(names))
    if not names or len(names)>MAX_INTENTS or len(training)+len(holdout)>MAX_EXAMPLES:
        raise learning.LearningError("paired evaluation exceeds supported example or intent bounds")
    if any(not isinstance(e.get("text"),str) or len(e["text"])>overlap.MAX_TEXT for e in [*training,*holdout]):
        raise learning.LearningError("paired evaluation exceeds supported message bounds")
    if sum(len(e["text"]) for e in holdout)>MAX_HOLDOUT_CHARACTERS:
        raise learning.LearningError("holdout text exceeds paired evaluation work bound")
    if any(set(e["labels"])-set(names) for e in [*training,*holdout]):
        raise learning.LearningError("review labels contain retired taxonomy intents; review labels before comparison")
    if len({e["example_id"] for e in [*training,*holdout]})!=len(training)+len(holdout):
        raise learning.LearningError("review example identifiers are not distinct")
    diagnostic,eligible=overlap.audit(training,holdout)
    screened_ids={e["example_id"] for e in eligible}
    parsed=mining.parse_taxonomy(taxonomy)
    predict=learning.predictor(model)
    full,clean=[],[]
    languages=defaultdict(list);clean_languages=defaultdict(list)
    for e in holdout:
        truth=set(e["labels"])
        kw=keyword_decisions(e["text"],parsed,names)
        probabilities=predict(e["text"])
        if any(not math.isfinite(v) or not 0<=v<=1 for v in probabilities.values()):
            raise learning.LearningError("learned probabilities are not finite and bounded")
        raw=learning.decide(probabilities)
        lr={name:raw.get(name,"uncertain") for name in names}
        row=(truth,kw,lr,probabilities)
        full.append(row)
        language=e.get("language") or "und"
        languages[language].append(row)
        if e["example_id"] in screened_ids:
            clean.append(row);clean_languages[language].append(row)
    def summary(rows):
        result = _summarize(rows,names)
        result['probability_reliability'] = reliability.summarize(rows,names,model['intents'],training)
        return result

    # Bound the subgroup response explicitly without altering aggregate counts.
    selected=sorted(languages,key=lambda code:(-len(languages[code]),code))[:MAX_LANGUAGE_GROUPS]
    return {"format":FORMAT,"taxonomy_sha256":digest(taxonomy),
            "keyword_projection":"unique_winner_present_tied_winners_abstain_others_absent",
            "learned_untrained_intents_are_abstentions":True,
            "all_holdout":summary(full),
            "without_detected_lexical_overlap":summary(clean) if diagnostic["complete"] else None,
            "lexical_overlap":diagnostic,
            "by_language":[{"language":code,"all_holdout":summary(languages[code]),
                           "without_detected_lexical_overlap":summary(clean_languages[code])
                           if diagnostic["complete"] else None} for code in selected],
            "language_groups_total":len(languages),"language_groups_displayed":len(selected),
            "trained_intents":sorted(model["intents"]),
            "untrained_intents":sorted(set(names)-model["intents"].keys()),
            "thresholds":dict(model["thresholds"]),
            "holdout_used_to_fit_or_select_thresholds":False,
            "holdout_independence_verified":False,"external_quality_verified":False,
            "message_text_or_example_ids_returned":False,"execution_authorized":False,
            "interpretation":"Same reviewer-labelled examples for both methods. Correctness includes abstention as not correct; selective accuracy and coverage are separate. Paired tests are descriptive, unadjusted and do not establish independent sampling or deployment readiness. Do not tune on these held-out results."}
