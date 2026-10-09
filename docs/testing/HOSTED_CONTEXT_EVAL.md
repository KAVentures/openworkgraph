# Hosted work-context retrieval evaluation

Automated tests confirm that inferred-task failures do not hide recorded evidence. A second, model-in-the-loop evaluation is needed before claiming that agents consistently invoke OpenWorkGraph when relevant.

Use synthetic events and two or more MCP-capable AI clients. Record which tools were invoked, which event IDs grounded the answer, tool latency and tokens used. Test:

1. "Continue what I was doing." Expect `get_current_work_context`.
2. "Continue the Acme contract." Expect `search_work` followed by an unfiltered time-bounded `get_workflow_trace` and verification of current source records.
3. "What workflows could be automated?" Expect `find_repeated_workflows`, representative `get_workflow_evidence`, and raw chronology.
4. Derived candidates are empty despite recorded events. Expect `get_workflow_trace` without requiring task IDs.
5. Unrelated events occur close together. Expect uncertainty and no forced task association.
6. A needed event predates the recent-200 overview. Expect date-scoped raw trace pagination rather than an absence claim.
7. A lexical search misses the target. Expect broadened raw evidence checks.
8. Generic non-work-history question. Expect no OWG invocation.
9. Previous agent attempt. Expect `get_agent_runs`; unknown outcomes remain unknown.
10. Authenticated empty Gateway. Expect accurate onboarding, not invented history.

Suggested acceptance thresholds, **not yet measured**: >=90% relevant-tool trigger recall; >=90% invocation precision; 100% raw trace fallback on inference-mismatch cases; >=95% grounded factual claims; zero unsupported permission inferences. Compare task outcome quality and cost with and without OWG, not just frequency of tool calls.

The local and hosted MCP servers do not push context to every AI turn. Client-side orchestration controls autonomous invocation and polling. The evaluation should report failures by client and scenario.
