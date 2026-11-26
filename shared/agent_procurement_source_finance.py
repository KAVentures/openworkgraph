"""Compare explicitly declared invoice amounts with minimized source amounts.

All values use original currency minor units, never FX or assumed decimal
exponents. A Stripe invoice's amount_paid is not a payout or booked revenue.
"""
import re

from .agent_procurement_source_outcome import project

_AMOUNT=re.compile(r"^(0|[1-9][0-9]{0,18})$")


def compare(receipt,start,end,now,previous_paid_at,currency,declared):
    result={"status":"unknown","reason":"source_predicate_not_reconfirmed",
            "source_amount_paid_minor_units":None,"difference_minor_units":None}
    outcome=project(receipt,start,end,now)
    if outcome["status"]!="confirmed" or outcome["paid_at_ms"]!=previous_paid_at:
        return result
    money=receipt.get("invoice_finance")
    if not isinstance(money,dict) or money.get("live_record_reported") is not True:
        return {**result,"reason":"live_invoice_amount_unavailable"}
    if money.get("currency")!=currency:
        return {**result,"reason":"source_currency_mismatch"}
    raw=money.get("amount_paid_minor_units")
    if not isinstance(raw,str) or not _AMOUNT.fullmatch(raw) or int(raw)>10**18:
        return {**result,"reason":"invalid_source_amount"}
    amount=int(raw)
    return {**result,"status":"match" if amount==declared else "mismatch",
        "reason":"exact_minor_unit_comparison","source_amount_paid_minor_units":raw,
        "difference_minor_units":str(amount-declared),
        "source_read_at_ms":outcome["source_read_at_ms"]}


def summarize(state):
    latest={row["slot_id"]:row for row in state.get("source_finance_checks",[])}
    groups={}
    for row in latest.values():
        key=(row["window_id"],row["currency"],row["arm"],row["reconciliation_sha256"])
        group=groups.setdefault(key,{"window_id":key[0],"currency":key[1],"arm":key[2],
            "reconciliation_sha256":key[3],"selected":0,"matched":0,"mismatched":0,"unknown":0,
            "known_declared_minor_units":0,"known_source_minor_units":0})
        group["selected"]+=1
        group[{"match":"matched","mismatch":"mismatched","unknown":"unknown"}[row["status"]]]+=1
        if row["status"]!="unknown":
            group["known_declared_minor_units"]+=int(row["declared_amount_paid_minor_units"])
            group["known_source_minor_units"]+=int(row["source_amount_paid_minor_units"])
    rows=[]
    for key in sorted(groups):
        g=groups[key]
        g["known_difference_minor_units"]=g["known_source_minor_units"]-g["known_declared_minor_units"]
        for field in ("known_difference_minor_units","known_declared_minor_units","known_source_minor_units"):
            g[field]=str(g[field])
        rows.append(g)
    return {"selected":len(latest),"matched":sum(g["matched"] for g in rows),
            "mismatched":sum(g["mismatched"] for g in rows),"unknown":sum(g["unknown"] for g in rows),
            "groups":rows,"complete_ledger_verified":False,"bank_settlement_verified":False,
            "realized_roi_verified":False}
