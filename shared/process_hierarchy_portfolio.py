"""Organization repository coverage over declared maps, not observed process flow."""
from __future__ import annotations

from collections import Counter, defaultdict
from fractions import Fraction
from itertools import combinations
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from .analysis_snapshot import content_digest

FORMAT="openworkgraph.repository-portfolio.v1"
MAX_MAPS=100
MAX_CATALOG=1000
MAX_REFERENCES=1000
MAX_OCCURRENCES=10000
MAX_PAGE=50
ID=Annotated[str,Field(pattern=r"^[A-Za-z0-9_-]{1,80}$")]
VIEWS={
    "processes":{"all","unmapped","reused","multiple_pins","needs_review","archived","unavailable"},
    "maps":{"all","needs_review","archived"},
    "occurrences":{"all","needs_review","multiple_pins"},
    "overlaps":{"all"},
    "membership":{"all","shared","left_only","right_only","different_pins"},
    "categories":{"all"},
    "capabilities":{"all"},
}


class Query(BaseModel):
    model_config=ConfigDict(extra="forbid",strict=True)
    view: Literal["processes","maps","occurrences","overlaps","membership","categories","capabilities"]="processes"
    filter: str=Field(default="all",max_length=40)
    focus_workflow_id: ID|None=None
    left_root: ID|None=None
    right_root: ID|None=None
    offset: Annotated[int,Field(ge=0,le=MAX_OCCURRENCES)]=0
    limit: Annotated[int,Field(ge=1,le=MAX_PAGE)]=25
    expected_fingerprint: str|None=Field(default=None,pattern=r"^[a-f0-9]{64}$")


def ref_key(ref):
    return ref["workflow_id"],ref["revision"],ref["content_sha256"]


def _needs_review(info):
    return not info.get("active_latest_independently_reviewed_model",False)


def _paths(nodes):
    indexed={node["id"]:node for node in nodes}
    result={}
    for node in nodes:
        path=[]
        at=node
        while at:
            path.append(at["process"]["workflow_id"])
            at=indexed.get(at["parent_id"])
        result[node["id"]]=list(reversed(path))
    return result


def build(organization_id,catalog,maps,resolutions):
    """Inputs come from one authenticated read snapshot; every map is validated.

    Identity reuse and exact-pin reuse are separate. No process's duration,
    case count, costs or benefits are added merely because it appears in a map.
    """
    by_process=defaultdict(list)
    by_root={}
    occurrences=[]
    sets={}
    state_counts=Counter()
    for artifact in sorted(maps,key=lambda m:m["root_workflow_id"]):
        root=artifact["root_workflow_id"]
        h=artifact["hierarchy"]
        paths=_paths(h["nodes"])
        items=[]
        for node in sorted(h["nodes"],key=lambda n:(paths[n["id"]],n["id"])):
            ref=node["process"]
            info=resolutions[ref_key(ref)]
            row={"root_workflow_id":root,"map_revision":artifact["revision"],"map_sha256":artifact["content_sha256"],
                 "node_id":node["id"],"workflow_path":paths[node["id"]],"level":len(paths[node["id"]]),
                 "parent_workflow_id":paths[node["id"]][-2] if node["parent_id"] is not None else None,
                 "workflow_id":ref["workflow_id"],"revision":ref["revision"],"content_sha256":ref["content_sha256"],
                 "reference_state":info["state"],"model_review":info.get("independent_model_review"),
                 "needs_review":_needs_review(info)}
            if info.get("available"):
                row["title"]=info["title"]
                row["latest_revision"]=info["latest_revision"]
            items.append(row);occurrences.append(row)
            by_process[row["workflow_id"]].append(row)
            state_counts[row["reference_state"]]+=1
        members={r["workflow_id"] for r in items}
        sets[root]=members
        by_root[root]={"root_workflow_id":root,"revision":artifact["revision"],"content_sha256":artifact["content_sha256"],
                       "title":h["title"],"category":h["category"],"capability":h["capability"],
                       "root_archived":catalog[root]["archived"],"occurrences":len(items),
                       "unique_processes":len(members),"repeated_occurrences":len(items)-len(members),
                       "needs_review_occurrences":sum(r["needs_review"] for r in items),
                       "reference_states":dict(sorted(Counter(r["reference_state"] for r in items).items())),
                       "maximum_level":max(r["level"] for r in items)}
    processes=[]
    for wid in sorted(set(catalog)|set(by_process)):
        rows=by_process.get(wid,[])
        pin_counts=Counter((r["revision"],r["content_sha256"]) for r in rows)
        roots=sorted({r["root_workflow_id"] for r in rows})
        parent_contexts=sorted({r["parent_workflow_id"] for r in rows if r["parent_workflow_id"] is not None})
        titles={r["title"] for r in rows if "title" in r}
        # Do not silently use a stale title as the current process title.
        latest_titles={r["title"] for r in rows if "title" in r and r["reference_state"]=="current"}
        row={"workflow_id":wid,"catalog_state":"unavailable" if wid not in catalog else
             "archived" if catalog[wid]["archived"] else "active",
             "current_title":next(iter(latest_titles)) if len(latest_titles)==1 else None,
             "available_pinned_titles":sorted(titles),"is_map_root":wid in by_root,
             "map_count":len(roots),"root_workflow_ids":roots,"occurrences":len(rows),
             "multiple_pins":len(pin_counts)>1,"reused_across_maps":len(roots)>1,
             "repeated_within_map":len(rows)>len(roots),
             "parent_workflow_ids":parent_contexts,
             "needs_review_occurrences":sum(r["needs_review"] for r in rows),
             "reference_states":dict(sorted(Counter(r["reference_state"] for r in rows).items())),
             "distinct_pins":len(pin_counts)}
        processes.append(row)
    overlaps=[]
    for a,b in combinations(sorted(sets),2):
        shared=sets[a]&sets[b]
        if not shared:
            continue
        union=sets[a]|sets[b]
        # Counts, not member lists in the pair summary; drill down by map/process.
        overlaps.append({"left_root":a,"right_root":b,"shared_process_count":len(shared),
                         "union_process_count":len(union),"left_unique_processes":len(sets[a]),
                         "right_unique_processes":len(sets[b]),
                         "jaccard_fraction":f"{len(shared)}/{len(union)}",
                         "jaccard":round(len(shared)/len(union),8),
                         "same_declared_membership":sets[a]==sets[b]})
    overlaps.sort(key=lambda r:(-Fraction(r["shared_process_count"],r["union_process_count"]),
                                -r["shared_process_count"],r["left_root"],r["right_root"]))
    def taxonomy(field):
        groups=defaultdict(list)
        for root,row in by_root.items():
            groups[row[field]].append(root)
        result=[]
        for name,roots in sorted(groups.items()):
            unique=set().union(*(sets[r] for r in roots))
            result.append({"label":name,"map_count":len(roots),"root_workflow_ids":sorted(roots),
                           "unique_processes":len(unique),"occurrences":sum(by_root[r]["occurrences"] for r in roots),
                           "maps_needing_review":sum(by_root[r]["needs_review_occurrences"]>0 for r in roots)})
        return result
    summary={"catalog_processes":len(catalog),"active_catalog_processes":sum(not r["archived"] for r in catalog.values()),
             "archived_catalog_processes":sum(r["archived"] for r in catalog.values()),
             "saved_maps":len(maps),"active_root_maps":sum(not r["root_archived"] for r in by_root.values()),
             "archived_root_maps":sum(r["root_archived"] for r in by_root.values()),
             "declared_occurrences":len(occurrences),"unique_referenced_processes":len(by_process),
             "distinct_exact_references":len(resolutions),
             "active_catalog_processes_without_any_map_occurrence":sum(not catalog[k]["archived"] and k not in by_process for k in catalog),
             "active_catalog_processes_without_own_root_map":sum(not catalog[k]["archived"] and k not in by_root for k in catalog),
             "unavailable_referenced_processes":sum(k not in catalog for k in by_process),
             "reused_across_maps_processes":sum(r["reused_across_maps"] for r in processes),
             "repeated_within_map_processes":sum(r["repeated_within_map"] for r in processes),
             "multiple_pin_processes":sum(r["multiple_pins"] for r in processes),
             "maps_needing_reference_review":sum(r["needs_review_occurrences"]>0 for r in by_root.values()),
             "reference_states":dict(sorted(state_counts.items())),
             "overlapping_map_pairs":len(overlaps)}
    data={"processes":processes,"maps":list(by_root.values()),"occurrences":occurrences,
          "overlaps":overlaps,"categories":taxonomy("category"),"capabilities":taxonomy("capability")}
    # Include every displayed/review-relevant field, not just totals; a
    # change in a single review state or path invalidates all old pages.
    fingerprint=content_digest({"format":FORMAT,"organization_id":organization_id,
                               "catalog":catalog,"summary":summary,"data":data})
    return {"format":FORMAT,"organization_id":organization_id,"fingerprint":fingerprint,
            "summary":summary,"data":data,"complete_bounded_declared_scan":True}


def membership(data,left,right):
    roots={r["root_workflow_id"] for r in data["maps"]}
    if left==right or left not in roots or right not in roots:
        raise ValueError("select two distinct saved repository map roots")
    left_rows=defaultdict(list)
    right_rows=defaultdict(list)
    for row in data["occurrences"]:
        if row["root_workflow_id"]==left:
            left_rows[row["workflow_id"]].append(row)
        elif row["root_workflow_id"]==right:
            right_rows[row["workflow_id"]].append(row)
    result=[]
    for wid in sorted(left_rows.keys()|right_rows.keys()):
        l,r=left_rows.get(wid,[]),right_rows.get(wid,[])
        def pins(rows):
            counts=Counter((x["revision"],x["content_sha256"]) for x in rows)
            return [{"revision":rev,"content_sha256":digest,"occurrences":count}
                    for (rev,digest),count in sorted(counts.items())]
        lp,rp=pins(l),pins(r)
        lkeys={(x["revision"],x["content_sha256"]) for x in lp}
        rkeys={(x["revision"],x["content_sha256"]) for x in rp}
        status="shared" if l and r else "left_only" if l else "right_only"
        result.append({"workflow_id":wid,"membership":status,"left_occurrences":len(l),"right_occurrences":len(r),
                       "left_pins":lp,"right_pins":rp,"different_exact_pin_sets":status=="shared" and lkeys!=rkeys,
                       "left_paths":[x["workflow_path"] for x in l],"right_paths":[x["workflow_path"] for x in r]})
    return result


def page(portfolio,query):
    if query.filter not in VIEWS[query.view]:
        raise ValueError("unsupported filter for the selected portfolio view")
    if query.offset and query.expected_fingerprint is None:
        raise ValueError("load the first portfolio page before requesting later pages")
    if query.expected_fingerprint is not None and query.expected_fingerprint!=portfolio["fingerprint"]:
        raise ValueError("repository portfolio or source basis changed; refresh the complete scan")
    focus=query.focus_workflow_id
    data=portfolio["data"]
    if query.view=="membership":
        if query.left_root is None or query.right_root is None:
            raise ValueError("select both saved repository map roots")
        membership_rows=membership(data,query.left_root,query.right_root)
    elif query.left_root is not None or query.right_root is not None:
        raise ValueError("map pair is only supported for membership comparison")
    else:
        membership_rows=[]
    if focus and not any(r["workflow_id"]==focus for r in data["processes"]):
        raise ValueError("selected workflow is not part of this repository portfolio")
    pin_conflicts={r["workflow_id"] for r in data["processes"] if r["multiple_pins"]}
    focus_roots={r["root_workflow_id"] for r in data["occurrences"] if r["workflow_id"]==focus}
    def include(row):
        view,f=query.view,query.filter
        if focus:
            if view in {"processes","membership"} and row["workflow_id"]!=focus:return False
            if view=="occurrences" and row["workflow_id"]!=focus:return False
            if view=="maps" and row["root_workflow_id"] not in focus_roots:return False
            if view=="overlaps" and not {row["left_root"],row["right_root"]}<=focus_roots:return False
            if view in {"categories","capabilities"} and not focus_roots.intersection(row["root_workflow_ids"]):return False
        if f=="all":return True
        if view=="processes":
            return {"unmapped":row["catalog_state"]=="active" and not row["occurrences"],
                    "reused":row["reused_across_maps"],"multiple_pins":row["multiple_pins"],
                    "needs_review":row["needs_review_occurrences"]>0,"archived":row["catalog_state"]=="archived",
                    "unavailable":row["catalog_state"]=="unavailable"}[f]
        if view=="maps":
            return row["root_archived"] if f=="archived" else row["needs_review_occurrences"]>0
        if view=="occurrences":
            return row["needs_review"] if f=="needs_review" else row["workflow_id"] in pin_conflicts
        if view=="membership":
            return row["different_exact_pin_sets"] if f=="different_pins" else row["membership"]==f
        return False
    rows=[r for r in (membership_rows if query.view=="membership" else data[query.view]) if include(r)]
    selected=rows[query.offset:query.offset+query.limit]
    return {k:v for k,v in portfolio.items() if k!="data"}|{
        "page":{"view":query.view,"filter":query.filter,"focus_workflow_id":focus,
                "left_root":query.left_root,"right_root":query.right_root,
                "offset":query.offset,"limit":query.limit,"matching_rows":len(rows),
                "next_offset":query.offset+len(selected) if selected and query.offset+len(selected)<len(rows) else None,
                "rows":selected},
        "limits":{"maps":MAX_MAPS,"catalog":MAX_CATALOG,"references":MAX_REFERENCES,
                  "occurrences":MAX_OCCURRENCES,"page":MAX_PAGE},
        "benefit_rollup_included":False,"execution_authorized":False,
        "dependency_graph_inferred":False,"model_reviews_propagated":False,
        "interpretation":"Counts describe current saved repository declarations, including archived-root maps. "
                         "Reuse and overlapping membership do not prove duplicate work, shared cases or conflicting business responsibilities. "
                         "Multiple exact pins indicate version diversity, not necessarily an error. Missing coverage means no occurrence in these saved maps, "
                         "not a missing business process. Taxonomy labels are exact declared strings; no hierarchy expansion, benefit aggregation or execution is inferred."}
