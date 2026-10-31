# Opportunity portfolio triage

The local automation pipeline now has a literal, case-insensitive keyword search
across idea title, details, source kind and reference. All entered words must
match; regex patterns are not interpreted. Combine search with a state and
priority quadrant, including an explicit **Not assessed** choice.

Sort by existing priority, modeled hours, ease, recent update or title.
Assessed zero values remain ahead of unassessed ideas in numeric sorts. These
controls change only the view, never assessment values or review decisions.
Search and priority changes operate on the already loaded bounded local list;
they do not send queries or idea text to another service.

The view states how many ideas are shown in the selected state. **Reset view**
clears all view controls. **Download state CSV** retains the existing export:
it includes the entire selected state, irrespective of search or priority.
The label and count message disclose this distinction. Modeled impact remains
an estimate from entered assumptions, not a claim of realized savings.
