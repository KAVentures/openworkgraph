"""Human-declared classifications; never canonical tasks or business-case joins."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

ExecutionID = Annotated[str,Field(pattern=r'^execution:[a-f0-9]{16}$')]


class ExecutionGrouping(BaseModel):
    model_config=ConfigDict(extra='forbid')
    format: Literal['openworkgraph.reviewed-execution-grouping.v1']='openworkgraph.reviewed-execution-grouping.v1'
    execution_ids: list[ExecutionID]=Field(default_factory=list,max_length=25)
    basis: Literal['client_declared_user_classification']='client_declared_user_classification'
    business_case_join_verified: Literal[False]=False
    business_intent_independently_verified: Literal[False]=False
    original_executions_modified: Literal[False]=False

    @model_validator(mode='after')
    def unique_ids(self):
        if len(set(self.execution_ids))!=len(self.execution_ids):
            raise ValueError('Each execution may appear only once in a grouping')
        return self
