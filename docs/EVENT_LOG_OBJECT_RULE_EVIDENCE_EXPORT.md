# Object-rule report downloads and example identities

After checking event-based object rules, **Download object rules JSON** saves the returned report. It can contain up to 50 violation examples per rule, including their event and object identifiers. **Download object rules CSV** saves one row per rule with literal rule labels, completeness and absent-label flags, counts, violation rate as a fraction, basis, log identity and append revision. It does not include individual example identifiers. Formula-like text is escaped for spreadsheet review.

A report containing supplied-assertion rules keeps the existing supplied-graph download and digest fields. A mixed report also offers the object-rule download. Changing the rule set invalidates these downloads until the rules are checked again.

The additive `violating_examples_exact` field contains separate `event_id`, `object_id` and `detail` values for each bounded example. The legacy `violating_examples` display strings remain available. Separate keys matter because an event or object ID may itself contain ` / `; the display string alone cannot unambiguously identify a synchronization pair. Use **Inspect violations** for paged retained evidence. These results describe the retained log, not verified source completeness or business compliance.
