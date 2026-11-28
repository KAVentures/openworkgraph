# Discover reusable workflow knowledge through local MCP

The existing local compact MCP tool `get_workflow_knowledge` supports an
optional discovery mode. When an agent does not know the workflow ID, it
can call, for example:

```text
get_workflow_knowledge(query="invoice approval", limit=10)
get_workflow_knowledge(summary_only=true, limit=20)
```

Discovery reads the existing authorized latest-record inventory (at most
100 records). All case-folded literal query terms must occur somewhere in
the returned workflow ID, title, process goal or procedure. Up to eight
terms and 200 query characters are supported. This is deterministic
navigation, not semantic matching, ranking, or a full catalog index. The
agent sees only candidate IDs, titles, revisions, content digest references,
and whether a process model is present; no full procedures, rules, or
matching snippets are added to the shortlist. The underlying local MCP
service still reads the bounded inventory through the existing API.

The response distinguishes source inventory truncation and shortlist
truncation. Absence of a match is not proof of absence. For relevant
candidates, call the original `get_workflow_knowledge(workflow_id=...)`
without discovery arguments and inspect the returned revision and digest.
The shortlist digest is a reference, not independently verified content.
Recheck applicability, unresolved questions and current permissions.
The exact-ID read and original list behavior are unchanged. Discovery
does not bypass the existing AI access checks, detail handling, or
reviewed-knowledge trust boundary and adds no write or execution action.

Use fresh selected workflow evidence separately when authoring an
implementation. Saved context is not observed evidence or policy. The
dashboard knowledge companion remains an optional separate export with
its own stronger local digest checks; this MCP mode does not produce that
exact companion. No change to cloud gateway or public plugin contracts
is made by this increment.

Run `python -m pytest -q tests/test_knowledge_discovery.py tests/test_knowledge_discovery_registration.py`.
The pure helper tests are independent of MCP installation. The registration
test exercises the actual registration function using substituted transport
and authorization, not a live installed connector. Full local MCP transport
and actual external-agent acceptance remain required.
