from __future__ import annotations

"""Local interactive administration for declared-policy proposals.

There is intentionally no network equivalent of the `apply` command. Source sync
may prepare immutable proposals, but it can never activate them.
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

from .policy_proposals import (
    PolicyProposalError,
    apply_policy_proposal,
    create_policy_proposal,
    list_policy_proposals,
    load_policy_proposal,
)
from .policy_sources import PolicySourceError, policy_source_status, sync_policy_sources


def _print_json(value: object) -> None:
    print(json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m server.policy_admin")
    sub = parser.add_subparsers(dest="command", required=True)

    propose = sub.add_parser("propose", help="validate a candidate manifest and create an immutable local proposal")
    propose.add_argument("candidate", type=Path)

    show = sub.add_parser("show", help="show a privacy-minimized proposal review")
    show.add_argument("proposal_id")

    sub.add_parser("list", help="list local policy proposals")
    sub.add_parser("sync-sources", help="scan approved local policy sources and prepare proposals without activation")
    sub.add_parser("source-status", help="show privacy-minimized policy source sync status")

    apply = sub.add_parser("apply", help="interactively activate one non-stale proposal")
    apply.add_argument("proposal_id")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(list(argv) if argv is not None else None)
    try:
        if args.command == "propose":
            proposal = create_policy_proposal(args.candidate)
            _print_json({
                "proposal_id": proposal["proposal_id"],
                "created_at": proposal["created_at"],
                "base_manifest_sha256": proposal.get("base_manifest_sha256"),
                "candidate_manifest_sha256": proposal["candidate_manifest_sha256"],
                "stale": proposal["stale"],
                "diff": proposal["current_diff"],
                "next": f"python -m server.policy_admin apply {proposal['proposal_id']}",
            })
            return 0
        if args.command == "show":
            _print_json(load_policy_proposal(args.proposal_id))
            return 0
        if args.command == "list":
            _print_json({"proposals": list_policy_proposals()})
            return 0
        if args.command == "sync-sources":
            _print_json(sync_policy_sources())
            return 0
        if args.command == "source-status":
            _print_json(policy_source_status())
            return 0
        if args.command == "apply":
            interactive = bool(sys.stdin.isatty() and sys.stdout.isatty())
            if not interactive:
                raise PolicyProposalError("apply refuses non-interactive stdin/stdout; use a local terminal")
            proposal = load_policy_proposal(args.proposal_id)
            if proposal.get("stale"):
                raise PolicyProposalError("proposal is stale; create a new proposal against the current manifest")
            print("Policy proposal review:")
            _print_json({
                "proposal_id": proposal["proposal_id"],
                "base_manifest_sha256": proposal.get("base_manifest_sha256"),
                "candidate_manifest_sha256": proposal["candidate_manifest_sha256"],
                "diff": proposal["current_diff"],
                "activation": proposal["activation"],
            })
            _print_json(apply_policy_proposal(args.proposal_id))
            return 0
    except (PolicyProposalError, PolicySourceError) as exc:
        print(f"policy admin error: {exc}", file=sys.stderr)
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
