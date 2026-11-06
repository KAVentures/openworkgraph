# M365 operator-cycle membership verification

Run `python -m server.m365_outlook_operator_cycle membership --help` after a reviewed extraction/append attempt. This is an independent read-only operator action; it does not change the existing `recover` decision or authorize retry.

Supply `--tenant`, `--mailbox`, `--folder`, `--grant-file`, `--candidate`, `--csv-path`, `--log-id`, `--confirm-log-id`, `--consent-local-read`, and optionally `--port`. Set `OWG_DASHBOARD_SESSION` for the current authorized local session. The command delegates to the existing authenticated source-event membership implementation and enforces an active provider grant, exact destination and scoped export. It reads only the bounded OWG loopback source CSV, not Graph.

Exit 0: all reviewed candidate source keys/case mappings present in the saved log. Exit 3: some candidate keys missing; manual reconciliation required. Exit 2: refused or unverifiable. Output never includes message contents or source IDs. All results explicitly refuse automatic retry and checkpoint advancement. A successful membership check does **not** prove provider completeness, write receipt durability, event payload equivalence or production tenant acceptance. See `docs/M365_OUTLOOK_EVENT_MEMBERSHIP.md` for 16 MiB limit and underlying contract.

Run `python -m pytest tests/test_m365_outlook_operator_membership.py`; native CI and authorized real-provider acceptance remain separate gates.
