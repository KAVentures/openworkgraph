"""Explicit offline management of scoped Stripe attempt-verification receipts.

No provider network calls, credentials, or implied authorization. The operator
must already be authorized to access the local journal and its actor scope.
"""
from __future__ import annotations

import argparse
import json
import sys

from .open_work_attempt_receipts import list_receipts, purge_expired_receipts, purge_scope


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Manage local scoped provider-attempt verification receipts (offline)")
    parser.add_argument("--journal-path", required=True, help="Explicit local SQLite journal path")
    parser.add_argument("--tenant", required=True, help="Exact tenant identity")
    parser.add_argument("--actor", required=True, help="Exact actor identity")
    commands = parser.add_subparsers(dest="command", required=True)
    history = commands.add_parser("history", help="Read latest 100 receipts for one exact attempt")
    history.add_argument("--attempt", required=True)
    retention = commands.add_parser("retention", help="Delete actor receipts older than N days")
    retention.add_argument("--older-than-days", type=int, required=True)
    retention.add_argument("--confirm-delete", action="store_true", required=True)
    purge = commands.add_parser("purge", help="Delete all receipts for one actor on revocation")
    purge.add_argument("--confirm-delete", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "history":
            result = {"receipts": list_receipts(args.journal_path, tenant=args.tenant,
                                                actor=args.actor, attempt=args.attempt)}
        elif args.command == "retention":
            result = {"deleted": purge_expired_receipts(args.journal_path, tenant=args.tenant,
                                                         actor=args.actor,
                                                         older_than_days=args.older_than_days)}
        else:
            result = {"deleted": purge_scope(args.journal_path, tenant=args.tenant, actor=args.actor)}
    except (ValueError, OSError) as exc:
        # Do not leak absolute paths, SQL content or customer data.
        print(json.dumps({"error": "invalid_scope_or_journal"}), file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
