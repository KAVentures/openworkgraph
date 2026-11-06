# Microsoft 365 next-round authenticated membership gate

The existing operator-controlled Microsoft Graph next-round extraction can now opt into a **fresh authenticated OWG source-event membership check** before any Graph GET.

Use the standalone `python -m server.m365_outlook_verified_next_round` command with all existing required arguments and explicit consents, plus `--require-event-membership`. The OWG dashboard session (`OWG_DASHBOARD_SESSION`) must be valid and the exact saved log ID confirmed. The existing provider grant must remain active. This reads the previously reviewed candidate and a bounded authenticated `sourcecsv` export, verifies individual source event keys and case mappings, and refuses the next Graph read if membership is incomplete or unverifiable.

This is **opt-in** for backward compatibility. The existing operator-cycle `extract` command does not yet expose the new flag; invoke the verified-next-round CLI directly to enforce it. A successful check only establishes local membership at read time. It does **not** prove Graph completeness, payload equality, safe replay of uncertain writes, or permission to advance a provider cursor. Existing human removal review and explicit read/export consents remain mandatory. Real Microsoft 365 tenant acceptance is still required.
