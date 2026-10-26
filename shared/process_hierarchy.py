"""Explicit repository decomposition; references never become executable calls."""
from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, field_validator, model_validator
from .analysis_snapshot import content_digest

ID = Annotated[str, Field(pattern=r'^[A-Za-z0-9_-]{1,80}$')]
Digest = Annotated[str, Field(pattern=r'^[a-f0-9]{64}$')]
Revision = Annotated[StrictInt, Field(ge=1,le=2147483647)]
Text = Annotated[str, Field(max_length=120)]
MAX_NODES = 100
MAX_LEVELS = 4  # Root is level one.


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class Reference(Strict):
    workflow_id: ID
    revision: Revision
    content_sha256: Digest


class Node(Strict):
    id: ID
    parent_id: ID | None = None
    process: Reference


class Hierarchy(Strict):
    format: Literal['openworkgraph.process-repository-hierarchy.v1'] = 'openworkgraph.process-repository-hierarchy.v1'
    organization_id: Annotated[str, Field(min_length=1,max_length=512)]
    title: Annotated[str, Field(min_length=1,max_length=120)]
    category: Text = ''
    capability: Text = ''
    nodes: list[Node] = Field(min_length=1,max_length=MAX_NODES)
    executable: Literal[False] = False
    propagates_process_review: Literal[False] = False
    execution_authorized: Literal[False] = False
    benefit_rollup_included: Literal[False] = False

    @field_validator('title','category','capability','organization_id')
    @classmethod
    def plain_text(cls,value):
        value=value.strip()
        value.encode('utf-8')
        if any(ord(c)<32 or 127<=ord(c)<=159 or c in '\u2028\u2029' for c in value):
            raise ValueError('single-line text required')
        return value

    @model_validator(mode='after')
    def tree(self):
        if not self.title or not self.organization_id:raise ValueError('title and organization required')
        indexed={n.id:n for n in self.nodes}
        if len(indexed)!=len(self.nodes):raise ValueError('node IDs must be unique')
        roots=[n for n in self.nodes if n.parent_id is None]
        if len(roots)!=1 or roots[0].id!='root':raise ValueError('one root node named root required')
        siblings=set()
        for node in self.nodes:
            key=(node.parent_id,node.process.workflow_id)
            if key in siblings:raise ValueError('duplicate process under one parent')
            siblings.add(key)
            seen=set();processes=set();current=node;level=0
            while current is not None:
                if current.id in seen or current.process.workflow_id in processes:raise ValueError('hierarchy cycle')
                seen.add(current.id);processes.add(current.process.workflow_id);level+=1
                if level>MAX_LEVELS:raise ValueError('hierarchy supports four total levels including root')
                if current.parent_id is None:break
                if current.parent_id not in indexed:raise ValueError('unknown parent')
                current=indexed[current.parent_id]
        return self

    def root(self):return next(n for n in self.nodes if n.id=='root')


class Save(Strict):
    expected_revision: Annotated[StrictInt, Field(ge=0,le=20)] = 0
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    hierarchy: Hierarchy
    user_confirmed: StrictBool = False
    organization_sharing_confirmed: StrictBool = False


class Remove(Strict):
    expected_revision: Annotated[StrictInt, Field(ge=1,le=20)]
    expected_access_revision: Annotated[StrictInt, Field(ge=1)]
    user_confirmed: StrictBool = False


class Saved(Strict):
    format: Literal['openworkgraph.saved-process-hierarchy.v1'] = 'openworkgraph.saved-process-hierarchy.v1'
    hierarchy: Hierarchy
    saved_at: str
    author_actor_id: str
