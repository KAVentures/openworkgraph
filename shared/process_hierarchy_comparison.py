"""Compare exact saved repository declarations; never infer execution edges."""
from collections import Counter,defaultdict
from typing import Annotated
from pydantic import Field,model_validator
from .process_hierarchy import Strict,Digest
from pydantic import StrictInt

class MapPin(Strict):
    revision: Annotated[StrictInt,Field(ge=1,le=20)]
    expected_content_sha256: Digest

class MapComparison(Strict):
    baseline: MapPin
    candidate: MapPin

    @model_validator(mode="after")
    def distinct(self):
        if self.baseline.revision==self.candidate.revision:
            raise ValueError("choose two distinct map revisions")
        return self


def _paths(hierarchy):
    by_id={n["id"]:n for n in hierarchy["nodes"]}
    paths={}
    for node in hierarchy["nodes"]:
        branch=[]
        current=node
        while current is not None:
            branch.append(current["process"]["workflow_id"])
            current=by_id[current["parent_id"]] if current["parent_id"] is not None else None
        paths[tuple(reversed(branch))]=node
    return paths


def compare(before,after):
    """Match exact workflow-ID ancestry paths, not reusable editor node IDs."""
    a,b=before["hierarchy"],after["hierarchy"]
    left,right=_paths(a),_paths(b)
    shared=set(left)&set(right)
    removed=set(left)-set(right)
    added=set(right)-set(left)
    def occurrence(path,nodes):
        node=nodes[path]
        return {"workflow_path":list(path),"node_id":node["id"],"process":node["process"]}
    matched=[]
    for path in sorted(shared):
        old,new=left[path],right[path]
        matched.append({"workflow_path":list(path),"baseline":occurrence(path,left),
                        "candidate":occurrence(path,right),
                        "reference_changed":old["process"]!=new["process"],
                        "editor_node_id_changed":old["id"]!=new["id"]})
    lost=[occurrence(p,left) for p in sorted(removed)]
    gained=[occurrence(p,right) for p in sorted(added)]
    lg,rg=defaultdict(list),defaultdict(list)
    for p in sorted(removed):lg[p[-1]].append(p)
    for p in sorted(added):rg[p[-1]].append(p)
    relocation=[]
    for wid in sorted(set(lg)&set(rg)):
        relocation.append({"workflow_id":wid,
            "removed_paths":[list(p) for p in lg[wid]],"added_paths":[list(p) for p in rg[wid]],
            "unique_unmatched_path_on_each_side":len(lg[wid])==len(rg[wid])==1,
            "relocation_verified":False})
    old_counts=Counter(p[-1] for p in left)
    new_counts=Counter(p[-1] for p in right)
    changes=[{"workflow_id":wid,"baseline_occurrences":old_counts[wid],"candidate_occurrences":new_counts[wid]}
             for wid in sorted(old_counts.keys()|new_counts.keys()) if old_counts[wid]!=new_counts[wid]]
    # Node IDs can be reused after deletion; report collisions independently.
    old_ids={n["id"]:p for p,n in left.items()}
    new_ids={n["id"]:p for p,n in right.items()}
    reused=[{"node_id":nid,"baseline_path":list(old_ids[nid]),"candidate_path":list(new_ids[nid])}
            for nid in sorted(old_ids.keys()&new_ids.keys()) if old_ids[nid]!=new_ids[nid]]
    return {"format":"openworkgraph.repository-map-comparison.v1",
        "baseline":{"revision":before["revision"],"content_sha256":before["content_sha256"]},
        "candidate":{"revision":after["revision"],"content_sha256":after["content_sha256"]},
        "root_workflow_id":left[next(p for p in left if len(p)==1)]["process"]["workflow_id"],
        "metadata_changes":[{"field":k,"baseline":a[k],"candidate":b[k]}
                            for k in ("title","category","capability") if a[k]!=b[k]],
        "counts":{"baseline_occurrences":len(left),"candidate_occurrences":len(right),
                  "matched_paths":len(shared),"removed_paths":len(lost),"added_paths":len(gained),
                  "changed_reference_paths":sum(r["reference_changed"] for r in matched),
                  "changed_editor_ids":sum(r["editor_node_id_changed"] for r in matched),
                  "editor_ids_with_changed_paths":len(reused),"possible_relocation_groups":len(relocation)},
        "matched_paths":matched,"removed_paths":lost,"added_paths":gained,
        "process_occurrence_count_changes":changes,"possible_relocation_groups":relocation,
        "editor_ids_with_changed_paths":reused,
        "reference_identity_basis":"exact_workflow_id_ancestry_path",
        "layout_order_changed":([n["id"] for n in a["nodes"]]!=[n["id"] for n in b["nodes"]]),
        "live_model_fields_read":False,"relocations_inferred":False,"review_propagated":False,
        "execution_authorized":False,"benefit_rollup_included":False,
        "interpretation":[
            "Exact workflow-ID ancestry paths identify declarations within a map, not observed business activity. Editor node IDs can be reused; changed IDs and paths are reported separately.",
            "Removed and added paths sharing a workflow ID are possible relocations only, including unique unmatched pairs. Moving an ancestor can change multiple descendant paths. No identity pairing or move is asserted.",
            "Only the two retained map declarations are compared. Model content, current child availability, independent review, dependency graphs and execution semantics are not inferred."]}
