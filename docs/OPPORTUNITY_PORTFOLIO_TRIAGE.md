# Opportunity portfolio triage

The local automation pipeline has a literal, case-insensitive keyword search
across idea title, details, source kind and reference, owner, team, and target
quarter. All entered words must match. Combine search with state and priority,
including **Not assessed**. Open **Planning filters** to choose exact owner, team,
or target quarter labels, or find ideas with those fields missing.

Sort by existing priority, modeled hours, ease, recent update or title.
Assessed zero values remain ahead of unassessed ideas in numeric sorts. These
controls change only the view, never assessment values or review decisions.
Search and priority changes operate on the already loaded bounded local list;
they do not send queries or idea text to another service.

The view states how many ideas are shown in the selected state. **Reset view**
clears all view controls. **Download shortlist CSV** and **Download shortlist JSON**
include the ideas in the filtered table in the current sort order, with their
saved revisions and planning labels. JSON also records the selected filters and
total number of ideas in the selected state. **Download state CSV** includes the
entire selected state. Modeled impact remains an estimate from entered assumptions.

[Compare opportunities](OPPORTUNITY_COMPARISON.md) side by side using their
saved assessment inputs and modeled results.
