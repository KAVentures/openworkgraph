"""Read-only Git object inventory for reviewing consolidation of predecessor PRs."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

FORMAT = "openworkgraph.pr-preservation-inventory.v1"
REPORT = "openworkgraph.pr-preservation-report.v1"
MAX_SOURCES = 200
SHA = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def _git(repo, *args, allowed=(0,)):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(GIT_NO_REPLACE_OBJECTS="1", GIT_OPTIONAL_LOCKS="0", GIT_NO_LAZY_FETCH="1",
               GIT_TERMINAL_PROMPT="0")
    p = subprocess.run(["git", "-C", str(repo), *args], env=env,
                       stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
    if p.returncode not in allowed:
        raise ValueError(f"Git {args[0]} failed: {p.stderr.decode('utf-8', 'replace').strip()}")
    return p.returncode, p.stdout


def _commit(repo, value):
    if type(value) is not str or not SHA.fullmatch(value):
        raise ValueError("full lowercase commit SHA required")
    _, output = _git(repo, "rev-parse", "--verify", value + "^{commit}")
    if output.strip().decode("ascii") != value:
        raise ValueError("pin is not the exact commit object")
    return value


def _ancestor(repo, base, head):
    return _git(repo, "merge-base", "--is-ancestor", base, head, allowed=(0, 1))[0] == 0


def _fields(obj, keys):
    if type(obj) is not dict or set(obj) != set(keys.split()):
        raise ValueError("unexpected or missing inventory fields")


def _tree(repo, head):
    _, data = _git(repo, "ls-tree", "-r", "-z", "--full-tree", head)
    result = {}
    for row in data.split(b"\0"):
        if row:
            header, path = row.split(b"\t", 1)
            mode, kind, oid = header.decode("ascii").split()
            result[path] = {"mode": mode, "kind": kind, "oid": oid}
    return result


def _changed(repo, base, head):
    # No rename detection: review source removal and destination addition separately.
    _, data = _git(repo, "diff-tree", "-r", "--no-commit-id", "--name-only", "--no-renames",
                   "--ignore-submodules=none", "--no-ext-diff", "-z", base, head)
    return sorted(set(p for p in data.split(b"\0") if p))


def _path_fields(path):
    # Lossless representation even for Git paths that are not UTF-8.
    return {"path": path.decode("utf-8", "backslashreplace"), "path_hex": path.hex()}


def _classify(before, source, candidate):
    if before == source:
        raise ValueError("inconsistent changed path inventory")
    if source is None:
        return "deletion_preserved" if candidate is None else "deletion_reintroduced"
    if candidate is None:
        return "missing"
    if candidate == source:
        return "exact"
    if candidate == before:
        return "reverted_to_base"
    return "changed_requires_review"


def audit(repo, inventory):
    _fields(inventory, "format candidate sources")
    if inventory["format"] != FORMAT:
        raise ValueError("unsupported inventory format")
    if _git(repo, "rev-parse", "--is-shallow-repository")[1].strip() != b"false":
        raise ValueError("full, non-shallow repository required")
    if _git(repo, "config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$",
            allowed=(0, 1))[0] == 0:
        raise ValueError("partial/promisor repository refused; use a complete local clone")
    # Legacy grafts can falsify ancestry even with replacement refs disabled.
    _, graft_path = _git(repo, "rev-parse", "--git-path", "info/grafts")
    grafts = Path(os.fsdecode(graft_path.rstrip(b"\n")))
    if not grafts.is_absolute():
        grafts = Path(repo) / grafts
    if grafts.exists() and grafts.stat().st_size:
        raise ValueError("repository has legacy grafts; ancestry not trustworthy")
    candidate = _commit(repo, inventory["candidate"])
    sources = inventory["sources"]
    if type(sources) is not list or not 1 <= len(sources) <= MAX_SOURCES:
        raise ValueError("require 1 to 200 predecessor records")
    ids = set()
    for src in sources:
        _fields(src, "pr base head")
        pr = src["pr"]
        if type(pr) is not int or pr <= 0 or pr in ids:
            raise ValueError("invalid or duplicate PR number")
        ids.add(pr)
        _commit(repo, src["base"])
        _commit(repo, src["head"])
        if not _ancestor(repo, src["base"], src["head"]):
            raise ValueError(f"PR {pr}: base must be an ancestor of original head")
    candidate_tree = _tree(repo, candidate)
    reports = []
    for src in sorted(sources, key=lambda x: x["pr"]):
        base_tree = _tree(repo, src["base"])
        source_tree = _tree(repo, src["head"])
        rows = []
        counts = {k: 0 for k in ("exact", "deletion_preserved", "missing", "deletion_reintroduced",
                                "reverted_to_base", "changed_requires_review")}
        for path in _changed(repo, src["base"], src["head"]):
            before, source, current = base_tree.get(path), source_tree.get(path), candidate_tree.get(path)
            status = _classify(before, source, current)
            counts[status] += 1
            rows.append({**_path_fields(path), "status": status, "base": before,
                         "source": source, "candidate": current})
        ancestor = _ancestor(repo, src["head"], candidate)
        unresolved = sum(counts.values()) - counts["exact"] - counts["deletion_preserved"]
        reports.append({**src, "source_head_is_ancestor": ancestor, "counts": counts,
                        "unresolved_paths": unresolved, "paths": rows,
                        "review_priority": "changed_or_missing" if unresolved else
                        "ancestry_not_preserved" if not ancestor else "exact_paths_only"})
    return {"format": REPORT, "candidate": candidate, "predecessors": reports,
            "functional_equivalence_established": False, "retirement_authorized": False,
            "merge_authorized": False}


def _unique(pairs):
    out = {}
    for key, value in pairs:
        if key in out:
            raise ValueError("duplicate inventory JSON key")
        out[key] = value
    return out


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("repository", type=Path)
    p.add_argument("inventory", type=Path)
    args = p.parse_args(argv)
    try:
        with args.inventory.open("rb") as f:
            raw = f.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("inventory exceeds 1 MB")
        result = audit(args.repository, json.loads(raw, object_pairs_hook=_unique))
    except (ValueError, OSError, subprocess.TimeoutExpired, RecursionError) as exc:
        p.error(str(exc))
    print(json.dumps(result, sort_keys=True, ensure_ascii=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
