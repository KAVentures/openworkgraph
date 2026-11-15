# Event-log CSV for spreadsheet review

The event-log export selector includes **CSV for spreadsheet review** and
**Source-ID CSV for spreadsheet review**. These produce `.review.csv` files
with `_review` column names. String cells beginning with `=`, `+`, `-` or `@`,
including after whitespace or a byte-order mark, are prefixed with an
apostrophe; cells beginning with a tab or line break are prefixed too. Other
values remain unchanged. These exports are intended for human review.

CSV has no standard cell type, and spreadsheet applications and import settings
vary. Check the receiving application's text-import behavior. Do not use review
files for re-import or exact identity exchange: column names and some values have
intentionally changed. Choose **CSV (exact interchange)** or **Source-ID CSV
(exact interchange)** when lossless re-import is needed. Those formats retain
source values exactly and should be treated accordingly when opening them in a
spreadsheet.

The review options use the existing owner-session-only export route, local log
scope, temporary-file cleanup and no-store response. They do not modify the
saved log, contact a provider, or grant AI access. Source-ID review requires the
same `source_case` notion and consistent source index as exact Source-ID CSV.

Tests cover formula prefixes and leading whitespace, actual CSV and Source-ID
exports, unchanged exact output, owner versus AI bearer authorization, and the
actual browser download with axe/mobile checks. Spreadsheet-specific behavior
and real customer data are not certified by synthetic CSV tests.
