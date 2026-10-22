"""Bounded declarative predicates over explicitly supplied JSON facts.

No expression evaluation, provider access, mutation, routing or permissions.
Missing/unsupported operands are unknown. Scalar equality is type-sensitive;
ordering accepts numbers only. No strings or booleans are coerced to numbers.
"""
from __future__ import annotations

from decimal import Decimal
import math
import re
from typing import Annotated, Literal

from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

Scalar = (Annotated[str,Field(strict=True,max_length=240)] |
          Annotated[int,Field(strict=True,ge=-(10**15),le=10**15)] |
          Annotated[float,Field(strict=True,allow_inf_nan=False,ge=-(10**15),le=10**15)] |
          Annotated[bool,Field(strict=True)] | None)


class RuleCondition(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    path:str=Field(min_length=1,max_length=240)
    operator:Literal['eq','ne','gt','gte','lt','lte','in','not_in','exists','not_exists']
    expected:Scalar | Annotated[list[Scalar],Field(max_length=20)] = None

    @field_validator('path')
    @classmethod
    def bounded_json_pointer(cls,value):
        if (not value.startswith('/') or len(value.split('/'))>9 or re.search(r'~(?![01])',value)
                or any(ord(char)<32 or 127<=ord(char)<=159 or 0xD800<=ord(char)<=0xDFFF for char in value)):
            raise ValueError('Use a bounded RFC6901 JSON pointer with at most eight segments')
        return value

    @model_validator(mode='after')
    def compatible_expected_value(self):
        values=self.expected if isinstance(self.expected,list) else [self.expected]
        if any(isinstance(value,str) and any(0xD800<=ord(char)<=0xDFFF for char in value) for value in values):
            raise ValueError('Expected strings must contain valid Unicode')
        if self.operator in {'exists','not_exists'} and self.expected is not None:
            raise ValueError('Existence predicates do not take an expected value')
        if self.operator in {'gt','gte','lt','lte'} and type(self.expected) not in {int,float}:
            raise ValueError('Ordering predicates require a finite numeric expected value')
        if self.operator in {'in','not_in'}:
            if not isinstance(self.expected,list) or not self.expected:
                raise ValueError('Membership predicates require a nonempty bounded scalar list')
        elif isinstance(self.expected,list):
            raise ValueError('Only membership predicates take an expected list')
        return self


class RulePredicate(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    match:Literal['all','any']='all'
    conditions:list[RuleCondition]=Field(min_length=1,max_length=8)


def validate_facts(facts):
    """Reject non-JSON data and unsafe sizes without echoing any fact values."""
    pending=[(facts,0)];count=0
    if type(facts) is not dict:raise ValueError('Case facts must be a bounded JSON object')
    while pending:
        value,depth=pending.pop();count+=1
        if count>5000 or depth>8:raise ValueError('Case facts exceed the structural bounds')
        if type(value) is dict:
            if len(value)>100 or any(type(key) is not str or len(key)>120 or
                    any(0xD800<=ord(char)<=0xDFFF for char in key) for key in value):
                raise ValueError('Case fact keys exceed the structural bounds')
            pending.extend((item,depth+1) for item in value.values())
        elif type(value) is list:
            if len(value)>100:raise ValueError('Case fact lists exceed the structural bounds')
            pending.extend((item,depth+1) for item in value)
        elif type(value) is str:
            if len(value)>4096 or any(0xD800<=ord(char)<=0xDFFF for char in value):
                raise ValueError('Case fact strings exceed the structural bounds')
        elif type(value) in {int,float}:
            if abs(value)>10**15 or (type(value) is float and not math.isfinite(value)):
                raise ValueError('Case fact numbers must be finite and bounded')
        elif value is not None and type(value) is not bool:
            raise ValueError('Case facts must contain only JSON values')


def _lookup(facts,path):
    value=facts
    for segment in path.split('/')[1:]:
        key=segment.replace('~1','/').replace('~0','~')
        if type(value) is dict and key in value:value=value[key]
        elif type(value) is list and re.fullmatch(r'0|[1-9][0-9]{0,3}',key) and int(key)<len(value):value=value[int(key)]
        else:return False,None
    return True,value


def _equal(actual,expected):
    if type(actual) in {int,float} and type(expected) in {int,float}:
        return Decimal(str(actual))==Decimal(str(expected))
    return type(actual) is type(expected) and actual==expected


def evaluate_predicate(predicate,facts):
    """Return only three-valued results/reason codes, never fact or rule values."""
    validate_facts(facts)
    if not isinstance(predicate,RulePredicate):predicate=RulePredicate.model_validate(predicate)
    results=[]
    for condition in predicate.conditions:
        present,actual=_lookup(facts,condition.path);operator=condition.operator
        if operator in {'exists','not_exists'}:
            matched=present if operator=='exists' else not present
            results.append({'status':'matched' if matched else 'not_matched','reason':'declared_existence_check'});continue
        if not present:
            results.append({'status':'unknown','reason':'required_fact_missing'});continue
        if type(actual) not in {str,int,float,bool,type(None)}:
            results.append({'status':'unknown','reason':'fact_type_incompatible'});continue
        if operator in {'gt','gte','lt','lte'}:
            if type(actual) not in {int,float}:
                results.append({'status':'unknown','reason':'fact_type_incompatible'});continue
            left,right=Decimal(str(actual)),Decimal(str(condition.expected))
            matched={'gt':left>right,'gte':left>=right,'lt':left<right,'lte':left<=right}[operator]
        elif operator in {'in','not_in'}:
            matched=any(_equal(actual,value) for value in condition.expected)
            if operator=='not_in':matched=not matched
        else:
            # Different scalar types are known unequal, with numbers handled
            # explicitly so Python's True == 1 cannot become a policy match.
            matched=_equal(actual,condition.expected)
            if operator=='ne':matched=not matched
        results.append({'status':'matched' if matched else 'not_matched','reason':'declared_scalar_comparison'})
    states=[item['status'] for item in results]
    if predicate.match=='all':status='not_matched' if 'not_matched' in states else 'unknown' if 'unknown' in states else 'matched'
    else:status='matched' if 'matched' in states else 'unknown' if 'unknown' in states else 'not_matched'
    return {'status':status,'conditions':results,'facts_authoritativeness_verified':False,
            'business_policy_compliance_verified':False,'execution_authorized':False}
