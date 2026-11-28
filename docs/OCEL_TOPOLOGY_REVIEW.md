# OCEL relationship connectivity and removal impact (R24)

From the retained object relationship comparison page, open **Review relationship
connectivity and removal impact**. Select one locally retained OCEL log,
inspect its revision and exact relationship/object-index fingerprints, optionally
match one literal qualifier (including the empty qualifier), and explicitly
confirm the static graph projection.

The complete bounded population includes every retained object, including
isolates. Distinct endpoint pairs collapse both directions and qualifiers into
one undirected edge for weak connectivity; self assertions are counted but do
not connect objects. The review refuses logs exceeding 20,000 objects or
100,000 retained assertions, even when a qualifier filter would reduce the graph.

Three evidence views show weak components, articulation objects and bridge
endpoint pairs. Articulation removes an object and its incident links and counts
formerly connected unordered pairs of surviving objects now in distinct parts.
Bridge impact removes every selected assertion between its endpoints, and counts
the newly disconnected unordered object pairs. A single-assertion bridge has
exactly one selected literal assertion; a bridge with several assertions must
not be interpreted as a single-assertion failure. Part sizes, multiplicities and
direction counts are provided. No combined-removal analysis is inferred.

The iterative low-link engine avoids recursion depth dependence. Each page pins
the retained revision, relationship digest, object index digest, qualifier and
view. Changed evidence or settings refuses stale requests. Page JSON downloads
contain literal local object identifiers and require confirmation. This is
mathematical connectivity of supplied static assertions, not a verified source
relationship, actual case membership, valid-time topology, causal impact,
operational loss, or recommendation to remove a relationship. Nothing is changed.
