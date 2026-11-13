"""Version-pinned human dependency declarations; never executable process calls."""
from collections import deque
import heapq
from typing import Annotated, Literal

from pydantic import Field, StrictBool, StrictInt, field_validator, model_validator
from .process_hierarchy import Strict, Reference, ID
from .analysis_snapshot import content_digest

MAX_REVISIONS=20
MAX_LINKS=20
MAX_ROOTS=100
MAX_PROCESSES=200


class Link(Strict):
    id: ID
    prerequisite: Reference
    relationship: Literal['uses_output','uses_policy']
    reason: Annotated[str,Field(min_length=1,max_length=500)]

    @field_validator('reason')
    @classmethod
    def text(cls,value):
        value=value.strip();value.encode('utf-8')
        if not value or any(ord(c)<32 or 127<=ord(c)<=159 or c in '\u2028\u2029' for c in value):raise ValueError('Use a nonblank single-line dependency reason')
        return value


class Declaration(Strict):
    format: Literal['openworkgraph.process-dependencies.v1']='openworkgraph.process-dependencies.v1'
    organization_id: Annotated[str,Field(min_length=1,max_length=512)]
    root_process: Reference
    links: list[Link]=Field(default_factory=list,max_length=MAX_LINKS)
    relationships_independently_verified: Literal[False]=False
    executable: Literal[False]=False
    propagates_process_review: Literal[False]=False
    execution_authorized: Literal[False]=False
    benefit_rollup_included: Literal[False]=False

    @field_validator('organization_id')
    @classmethod
    def org(cls,value):
        value.encode('utf-8')
        if not value.strip() or any(ord(c)<32 or 127<=ord(c)<=159 or c in '\u2028\u2029' for c in value):raise ValueError('Use a valid organization identifier')
        return value

    @model_validator(mode='after')
    def references(self):
        if len({link.id for link in self.links})!=len(self.links):raise ValueError('Dependency IDs must be unique')
        if len({link.prerequisite.workflow_id for link in self.links})!=len(self.links):raise ValueError('Declare each prerequisite process once')
        if any(link.prerequisite.workflow_id==self.root_process.workflow_id for link in self.links):raise ValueError('A process cannot depend on itself')
        return self


class Save(Strict):
    expected_revision: Annotated[StrictInt,Field(ge=0,le=MAX_REVISIONS)]=0
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    dependencies: Declaration
    user_confirmed: StrictBool=False
    organization_sharing_confirmed: StrictBool=False


class Remove(Strict):
    expected_revision: Annotated[StrictInt,Field(ge=1,le=2147483647)]
    expected_access_revision: Annotated[StrictInt,Field(ge=1)]
    user_confirmed: StrictBool=False


class Saved(Strict):
    format: Literal['openworkgraph.saved-process-dependencies.v1']='openworkgraph.saved-process-dependencies.v1'
    dependencies: Declaration
    author_actor_id: str
    saved_at: str


def topology(declarations):
    """Global identity graph, deliberately independent of grouping-map ancestry."""
    if len(declarations)>MAX_ROOTS:raise ValueError('Dependency scan exceeds one hundred roots')
    nodes=set();edges=[]
    for declaration in declarations:
        root=declaration['root_process']['workflow_id'];nodes.add(root)
        for link in declaration['links']:
            target=link['prerequisite']['workflow_id'];nodes.add(target)
            edges.append({'consumer':root,'prerequisite':target,'link_id':link['id'],'relationship':link['relationship']})
    if len(nodes)>MAX_PROCESSES:raise ValueError('Declared dependency graph exceeds two hundred processes')
    outgoing={node:[] for node in nodes};incoming={node:[] for node in nodes}
    for edge in edges:
        outgoing[edge['consumer']].append(edge['prerequisite']);incoming[edge['prerequisite']].append(edge['consumer'])
    degrees={node:len(outgoing[node]) for node in nodes};ready=[node for node in nodes if degrees[node]==0];heapq.heapify(ready);ordered=[]
    while ready:
        node=heapq.heappop(ready);ordered.append(node)
        for consumer in sorted(incoming[node]):
            degrees[consumer]-=1
            if degrees[consumer]==0:heapq.heappush(ready,consumer)
    if len(ordered)!=len(nodes):raise ValueError('Declared process dependencies must not contain a cycle')
    return {'process_ids':sorted(nodes),'edges':sorted(edges,key=lambda e:(e['consumer'],e['prerequisite'],e['link_id'])),
            'prerequisite_first_order':ordered}


MAX_EXPLAINED_PATHS = 30
MAX_EXPLANATION_HOPS = 12


def impact(graph, focus):
    """Full declared reachability plus bounded, deterministic shortest-path reasons.

    The existing distance lists are NOT shortened. Optional path explanations
    expose only the already-shared structural link IDs and relationship kinds;
    owner-entered reason text stays in the per-root declaration manifest.
    Neither reachability nor a path establishes business causality or permission.
    """
    if focus not in graph["process_ids"]:
        raise ValueError("Choose a process from the declared graph")
    outgoing = {node: [] for node in graph["process_ids"]}
    incoming = {node: [] for node in graph["process_ids"]}
    for edge in graph["edges"]:
        outgoing[edge["consumer"]].append((edge["prerequisite"], edge))
        incoming[edge["prerequisite"]].append((edge["consumer"], edge))

    def reach(adjacency):
        pending = deque([focus])
        distance = {focus: 0}
        parents = {}
        while pending:
            node = pending.popleft()
            # Stable tie-breaking for diamonds and equivalent shortest routes,
            # independent of database scan and declaration insertion order.
            for neighbor, edge in sorted(adjacency[node], key=lambda item: (item[0], item[1]["link_id"])):
                if neighbor not in distance:
                    distance[neighbor] = distance[node] + 1
                    parents[neighbor] = (node, edge)
                    pending.append(neighbor)
        rows = [
            {"workflow_id": node, "distance": distance[node]}
            for node in sorted(distance, key=lambda name: (distance[name], name))
            if node != focus
        ]
        paths = []
        withheld_for_length = 0
        withheld_for_count = 0
        for row in rows:
            if len(paths) >= MAX_EXPLAINED_PATHS:
                withheld_for_count += 1
                continue
            if row["distance"] > MAX_EXPLANATION_HOPS:
                withheld_for_length += 1
                continue
            chain = [row["workflow_id"]]
            edges = []
            cursor = row["workflow_id"]
            while cursor != focus:
                previous, edge = parents[cursor]
                edges.append({key: edge[key] for key in ("consumer", "prerequisite", "link_id", "relationship")})
                chain.append(previous)
                cursor = previous
            chain.reverse()
            edges.reverse()
            paths.append({
                "workflow_id": row["workflow_id"],
                "distance": row["distance"],
                "nodes_from_focus": chain,
                "declared_links_from_focus": edges,
            })
        return rows, {
            "paths": paths,
            "matched_processes": len(rows),
            "explained_processes": len(paths),
            "withheld_for_path_limit": withheld_for_count,
            "withheld_for_hop_limit": withheld_for_length,
            "complete": not (withheld_for_count or withheld_for_length),
        }

    prerequisites, forward_paths = reach(outgoing)
    potential_consumers, reverse_paths = reach(incoming)
    return {
        "focus": focus,
        "prerequisites": prerequisites,
        "potential_consumers": potential_consumers,
        "declared_paths": {
            "prerequisites": forward_paths,
            "potential_consumers": reverse_paths,
            "max_explanations_per_direction": MAX_EXPLAINED_PATHS,
            "max_hops_per_explanation": MAX_EXPLANATION_HOPS,
            "direction": "nodes_from_focus follows the selected traversal; each link retains consumer_to_prerequisite direction",
            "basis": "saved_human_dependency_declarations",
            "business_causality_verified": False,
            "permissions_granted": False,
        },
        "basis": "saved_human_dependency_declarations",
        "causal_impact_verified": False,
        "complete_business_dependencies_verified": False,
        "execution_authorized": False,
    }
