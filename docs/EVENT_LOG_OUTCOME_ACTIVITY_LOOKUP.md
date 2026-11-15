# Activity-based outcome analysis

For the “case contains activity” and “case ends with activity” outcomes, the
activity dropdown suggests up to 500 frequent labels. Enter an exact label or
use **Find labels** to search the selected log's current case notion and applied
activity mapping. A selected exact label overrides the suggestion and is matched
literally, including whitespace and Unicode. An unknown label may match no
cases. Choosing a suggestion clears the exact field.

The lookup returns up to 100 labels per page and does not return event rows.
The existing local outcome-analysis request then uses the chosen label. Results
are descriptive associations, not verified causes or business outcomes; the
search and analytics still require database work proportional to the log.
