# Complete native shadow roster in the Agents dashboard

This implements the existing package-pinned roster (#1680) in the local
Agents dashboard without requiring the command line. It complements the
existing native trace review and staged skill preparation.

## Before testing

Verify the extracted skill package independently using the staging verifier.
Open **Agents / Plan and reconcile a complete native AI shadow test**.
Select the original manifest JSON and enter the independently retained
package SHA-256. Declare the entire held-out test population, with opaque
case IDs, independently reviewed expected frameworks and tool display labels.
Confirm the full roster and click Freeze test roster. Download both the
original plan and the complete blank assignment worklist. Retain the plan
digest independently before running the cases in the separately restricted
test client. Input edits clear the prepared downloads and confirmation.

## After testing

Record the actual root execution ID or null for each planned case. Do not
drop cases. Capture the original trace and its pin in the existing native
shadow evidence panel. In the roster panel select the original frozen plan,
original trace and completed assignments, with independent plan/trace pins.
Reconcile the full population. All five existing roster dispositions are
shown, with exact structural findings and unassigned returned run counts.
Download the canonical report. Changing review inputs clears the report.

The two data routes require a local dashboard session, reject duplicate JSON
keys and nonfinite numbers, bound the request to 18 MiB, the original trace
to 8 MiB and each other document to 256 KiB. No new storage or inference.
The original canonical documents are carried as strings rather than
browser-reserialized JSON. The browser uses memory only for these artifacts.

This does not prove prospective enrollment timing, independent package
installation, actual skill invocation, permission compliance or a business
outcome. Continue to verify all of those independently. No execution or
rollout is authorized. The existing CLI remains available.

## Validation

Run `python -m pytest -q tests/test_native_shadow_roster.py tests/test_native_shadow_roster_routes.py`.
The router tests exercise the actual existing roster and trace reviewers,
with dashboard authentication substituted. The existing real Chromium
agent-run regression includes the full plan-to-review round trip against
a retained trace snapshot, and refusal of omitted cases. Full combined
repository and installed-client acceptance remain required.
