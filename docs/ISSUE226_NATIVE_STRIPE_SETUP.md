# Issue #226 — Native Stripe source connection

This is an additional **opt-in local user setup** for the existing read-only Stripe source adapter. It makes source reads work when the macOS app is opened in Finder or the Windows app is started normally, without inheriting shell environment variables.

It is **not** organization-wide OAuth, Stripe Connect authorization, automated token rotation, or evidence of bank settlement.

## Setup and revocation

Open OpenWorkGraph's authenticated local dashboard, then **Privacy → Optional source connections → Manage Stripe read-only source** in Basic mode, or **Advanced → Organization → Manage Stripe read-only source**. Organization enrollment and shell environment variables are not required. Enter a Stripe account identifier beginning with acct_ and a **restricted** key beginning with rk_test_ or rk_live_. The key must have only the Stripe Account, Invoices and Refunds read permissions required by the existing adapter; unrestricted sk_ keys are rejected. Confirm the separate local status-read permission.

The setup endpoint makes only one verification GET to Stripe's fixed /v1/account endpoint and confirms the supplied key belongs to that account. Only then is the key stored in the signed-in user's macOS Keychain or Windows Credential Manager. Metadata in the local OWG auth directory stores only the account ID and an enabled flag (0600 mode on POSIX). Keys never appear in returned JSON, MCP records, files containing workflow evidence, shell arguments or the organization Gateway.

Only a process-scoped OWG dashboard session can configure, inspect status or disconnect; ordinary local API/MCP bearer access is rejected. An actual status read still requires an existing retained exact opaque record and explicit source refresh. The local user can disconnect in the same panel. Disconnect removes enabled metadata before trying vault deletion, so a temporarily locked vault does not keep provider reads authorized. Revoke the key independently in Stripe when needed.

Existing environment-based source setup remains fully compatible. If an explicit source-read ON/OFF environment override is present, OWG does not silently substitute local keychain credentials. The app remains default OFF without a specific local opt-in. Linux and unavailable/locked native vaults fail closed.

## What this does not prove

An invoice reported paid can include an external/manual payment. A Stripe refund with processor state succeeded does not prove that funds reached a customer's bank account. No customer acknowledgement, human handoff, or write permission is implied. This is a **local single-account setup**, not a company-managed Stripe authorization lifecycle.

Synthetic tests verify revocation, account mismatch, native-unavailable behavior and dashboard session isolation. Before rollout, run a real signed macOS + packaged Windows test using a consenting Stripe TEST account and restricted key. Confirm account/invoice/refund reads from Finder/Start Menu, keychain permission denial/lock behavior, uninstall and disconnect, wrong-account fail-closed, key revocation, and absence of credentials from logs, work evidence, MCP output or Gateway uploads. Real Stripe credentials were not provided for this PR; do not claim live provider integration was tested.

The separate Jira OAuth connection/refresh flow, formal organizational authorization, real provider receipts, independently accepted handoffs and actual agent A/B/C benchmark remain open acceptance gates for issue #226. Jira stays an experimental, operator-provisioned adapter and is intentionally not offered as a user-connectable Settings integration until evaluation justifies building that authorization lifecycle. Agents can use separately authorized Jira connectors in the meantime.
