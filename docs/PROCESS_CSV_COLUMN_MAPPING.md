# Map an authorized business-system event-log export

The Evidence tab can minimize a CSV with different header names or additional
columns before local analysis. Select the file, open **Map columns from another
event-log export**, choose Comma, Semicolon or Tab, and select **Inspect columns
on this device**. Inspection reads headers and validates row structure entirely
inside the browser. It makes no API request and displays no event payload sample.

Select three distinct columns for case identifier, activity label and event
timestamp. Duplicate or blank header names are distinguished by column position.
Only unique exact OWG header matches are preselected; nothing is applied
automatically. Review the choices, confirm **exclude every other column from
local analysis**, then choose **Use mapped columns for next analysis**.

The structural mapping creates an in-memory UTF-8 CSV with `case_id,activity,timestamp`.
Optional [participant/team columns](PROCESS_PARTICIPANT_COHORTS.md) require
separate review and replace their native values with import-scoped labels.
All unselected column headers and values are excluded, including multiline
fields. Values are quoted correctly and outer whitespace is trimmed as in the
existing importer. Select **Analyze selected CSV** to send that minimized log to
the authenticated local OWG API. Inspection, choosing fields and applying the
mapping do not upload a file. Separate retention confirmation, if used, saves
the same minimized log; mapping never causes retention itself.

The selected columns must still satisfy the regular mining contract: explicitly
supplied case IDs, short structural activity labels and offset-aware ISO-8601
timestamps. There is no inferred case join, guessed timestamp format, activity
renaming, business-content extraction or permission to execute historical work.
Case-ID authoritativeness, log completeness and employee consent must come from
the customer's authorized source/export workflow. Unused fields are never sent
for AI classification or semantic inference.

The parser supports quoted delimiters, escaped double quotes, embedded newlines,
CRLF/LF/CR records, BOM-prefixed UTF-8 and empty unquoted lines. It rejects invalid
UTF-8, NUL bytes, malformed quoting, unequal row widths, headers longer than 120
characters, more than 100 columns, more than 5,000 event rows, or raw/mapped files
over 1 MiB. Selecting the same column for multiple required fields is rejected.
These bounds supplement the existing server validation, which remains unchanged.

File content and mappings remain in browser memory. Nothing is written to browser
storage. Choosing another file, opening a retained log or opening a selected
source projection discards the previous mapping and invalidates pending
inspection. Changes to the delimiter or mapping require another explicit review
before analyzing with the new choices. Files already using OWG's documented CSV
schema can skip mapping and use the original analysis flow.

Implementation: `dashboard/process_csv_mapping.js` provides the bounded offline
parser/minimizer; the existing mining workspace owns the explicit selection and
analysis controls. No new source connector, collector, external parser service,
model provider, data table or normal desktop capture behavior is introduced.

Validation exercises quoting/delimiters, multiline unused fields, duplicate
headers, exact minimization, malformed files, UTF-8/bounds, pending inspection,
explicit confirmation and actual outgoing browser/API bytes. The owned browser
fixture records only incoming header shape and synthetic-marker presence for
the test; that fixture instrumentation is not part of production OWG.

For several exports already sharing a source-of-truth case key, use the
[reviewed shared-key import](REVIEWED_SHARED_CASE_IMPORTS.md) flow. It maps each
source independently and preserves event origins without guessing equivalence
from names, activity or time.
