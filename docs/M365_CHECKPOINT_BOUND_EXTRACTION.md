# Microsoft 365 checkpoint-bound incremental extraction

The existing operator-cycle `extract` command can now be bound to a previously
**committed** checkpoint by supplying both `--ledger-dir` and
`--expected-generation N`. Both are required together; the generation must be
a positive integer. Existing consent flags and the separate verified handoff
remain required. The operator must supply the previous candidate and log ID.

Before any Microsoft Graph GET, the command reads the scoped, private checkpoint
ledger with a current grant, checks the exact committed generation, and matches
the predecessor candidate CSV digest and destination log ID. Missing, stale,
conflicting or corrupted evidence refuses the extraction without a Graph read.
The private cursor and grant are not included in CLI output. The ordinary
handoff-only path remains available for backward compatibility.

This does **not** authorize automatic append, checkpoint advancement, or
retry after an uncertain OWG write. Review the new candidate and removals,
explicitly append, verify membership and handoff, and separately commit the
next checkpoint. A generation mismatch requires an operator to inspect the
current ledger and reconcile state rather than retry blindly.

Test with `python -m unittest tests.test_m365_operator_checkpoint_bound`.
This is a checkpoint-bound operator increment, **not** a completed dashboard
transaction or real Microsoft tenant acceptance.
