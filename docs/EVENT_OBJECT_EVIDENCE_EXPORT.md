# Object event evidence downloads

From an object's recorded-event history, open an event's details and choose a download. Both formats include the selected event, its retained attributes and resource, the selected object's recorded attribute states at that time, and every retained object link for that event. These files include literal identifiers and potentially sensitive retained values. The download is an explicit local dashboard-owner action.

JSON (`openworkgraph.object-event-evidence.v1`) preserves exact strings and includes all retained conflicting values. The CSV is a spreadsheet-review projection: formula-like cells are prefixed with an apostrophe, so it is not a lossless interchange format. `value_present` distinguishes an empty recorded value from a row without a value. Rows identify the event, event attributes, object states, and participant qualifiers separately.

The snapshot uses the reviewed append revision and one database read transaction. If the revision has changed, reopen the event history. To keep export size bounded, an event with more than 5,000 distinct linked objects or 20,000 retained links cannot be exported; the request fails rather than producing a partial file. The existing participant browser remains available for paged review.

Completeness applies to retained records for the event. Recorded links and values do not establish verified business relationships or business state, and source completeness is unknown.
