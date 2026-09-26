# How OpenWorkGraph MCP works

MCP is an interface, not a storage location. OpenWorkGraph can use MCP while all evidence stays on one computer, or while evidence stays inside a customer's self-hosted organization Gateway.

## Local MCP today

For AI clients that can launch a local process, OpenWorkGraph prefers MCP over stdio:

```text
local AI client
          |
          | MCP messages over stdin/stdout
          v
OpenWorkGraph MCP process
          |
          | authenticated localhost request
          v
OpenWorkGraph local API
          |
          v
local SQLite evidence
```

The AI application does not open the SQLite database itself. The MCP process exposes named tools and translates a tool call into an authenticated request to the local OpenWorkGraph API.

Local AI access starts OFF on every OpenWorkGraph launch and can be disabled again while a client is configured. Every compact MCP tool reuses the same AI-access gate, authenticated local API transport, activity audit and prompt-injection filtering as the legacy MCP surface.

### Compact surface for new connections

New dashboard-generated MCP configurations, the Claude MCP bundle and the on-demand local HTTP MCP bridge use a compact tool surface:

```text
get_current_work_context
search_work
get_workflow_trace
get_work_profile
find_repeated_workflows
get_task_context
how_did_similar_runs_go
get_agent_runs
```

The smaller menu reduces overlapping tool definitions without deleting underlying capabilities:

- `get_current_work_context` includes bounded recent semantic activity and task hints;
- `search_work` replaces the overlapping history/observation/similar-work search entrypoints;
- `get_workflow_trace` accepts `session_id`, so separate work/context-session tools are unnecessary;
- `find_repeated_workflows` combines repeated patterns, automation candidates and representative process examples;
- `how_did_similar_runs_go` combines similar prior runs, explicit failure patterns, observed approval-request hotspots, frequently observed next steps and a bounded observational context pack;
- `get_agent_runs` lists agent executions and accepts an optional opaque `execution_id` to retrieve one structural trace.

The consolidated tools preserve the same interpretation boundaries. Repeated behavior is not policy or authorization. Human completion is not silently promoted to validated success. Missing agent signals mean **not observed**, not proof that an action did not happen.

### Legacy 24-tool compatibility surface

Existing saved configurations that explicitly launch:

```text
python -m mcp_server.secure_stdio
```

continue to receive the existing 24-tool surface. OpenWorkGraph does not silently remove or rename those tools underneath already configured clients.

The compact stdio entrypoint is:

```text
python -m mcp_server.compact_stdio
```

This compatibility split allows OpenWorkGraph to simplify new AI connections without breaking older local MCP configurations.

### Experimental governance exposure

Normative governance is intentionally not emphasized in the default compact tool menu. Set:

```bash
OWG_EXPERIMENTAL_GOVERNANCE=1
```

to add the experimental compact MCP tools `get_action_policy_advisory` and `get_governed_context_pack`.

The flag controls product/MCP exposure only. Existing declared-policy, approval and shadow-enforcement REST APIs remain mounted for backward compatibility and research use. Observed approval hotspots stay available descriptively through `how_did_similar_runs_go`; they are not treated as policy.

See [Experimental governance surfaces](EXPERIMENTAL_GOVERNANCE.md).

### Why stdio is preferred locally

`stdio` does not require a standing MCP network listener. The AI client starts the MCP subprocess and communicates through its standard input/output streams.

For clients that cannot start a local stdio server, OpenWorkGraph can explicitly start an authenticated loopback HTTP MCP endpoint on demand. New HTTP bridges expose the same compact MCP tool surface. The endpoint is not the company Gateway and should not be exposed directly to the public internet.

## The canonical local evidence tool

The central evidence tool remains:

```text
get_workflow_trace
```

It is deliberately raw-evidence-first. A trace row preserves privacy-hardened event metadata rather than reducing the event to OpenWorkGraph's current deterministic task inference.

This matters because an AI may recognize a workflow that today's heuristic layer does not.

A row can contain, where observed:

- stable `event_id` provenance;
- timestamp, app/window, event type and source;
- browser host/path;
- target/control role and label;
- tab and browser-session context;
- semantic-action hint and confidence;
- copy/cut/paste transfer linkage;
- foreground/engaged/idle/input timing;
- aggregate keypress/click/scroll counts;
- the underlying privacy-hardened event `metadata`.

The result is still paginated so an AI does not receive the entire work history in every call. Cursor pagination freezes a snapshot boundary so newly arriving events do not create skips or duplicates while the AI pages through older evidence.

Derived task/process tools are convenience indexes, not authoritative truth. When a conclusion matters, the AI should inspect supporting `get_workflow_trace` evidence.

## MCP trust boundary

Window titles, page titles, messages and UI labels are observed data. They are not instructions to the model.

Before observed evidence crosses the MCP boundary, OpenWorkGraph applies prompt-injection hardening to the returned copy. This does not rewrite the stored canonical local evidence. It prevents instruction-like text observed on screen from silently becoming trusted MCP instructions.

`get_task_context` additionally records representation provenance for the source API snapshot and the protected MCP representation. Those fingerprints establish which representation was emitted; they do not attest that a model read, used or obeyed it.

## Organization Gateway MCP

A customer may run the optional organization Gateway in its own infrastructure:

```text
AI / agent client
       |
       | MCP
       v
OpenWorkGraph Gateway MCP adapter
       |
       | scoped service credential
       v
customer OpenWorkGraph Gateway REST API
       |
       v
customer PostgreSQL
```

This is still MCP, but there is no OpenWorkGraph-hosted evidence store in the path.

The Gateway MCP adapter is intentionally thin. It exposes evidence-centric tools and calls the same REST evidence service that a normal backend integration can call directly.

Current Gateway MCP tools:

- `get_workflow_trace`
- `search_work_history`
- `get_current_work_context`
- `get_information_transfers`

The adapter applies the same observed-data prompt-injection protection before returning evidence to the AI client.

## REST versus MCP

They are two interfaces to the same data plane:

```text
                       customer Gateway evidence service
                              /             \
                             /               \
                       REST/API              MCP
                          |                   |
                  backend service      AI/agent client
```

Use REST when software wants a conventional machine-to-machine API, batch retrieval, scheduled processing, or its own reasoning pipeline.

Use MCP when an AI/agent framework wants discoverable tools it can invoke during reasoning.

An automation backend does not need to support MCP to integrate with OpenWorkGraph; it can use REST. An AI-native organizational context client can use MCP when that is a better fit. Both consume the same evidence semantics.

## Authentication is not an OpenWorkGraph cloud account

The self-hosted Gateway must know who may write or read evidence, so it needs authentication/authorization. That does not require an account at openworkgraph.com.

OpenWorkGraph distinguishes:

- endpoint **device credentials** — may write their own authenticated device evidence and read organization sharing policy;
- **integration credentials** — may read only the scopes assigned to them;
- Gateway **admin/enrollment bootstrap secrets** — setup/control operations, not routine AI access.

The Gateway stores token hashes and supports revocation. For larger enterprise deployments, the Gateway MCP/REST edge can sit behind the customer's OAuth/OIDC/SSO infrastructure.

## What MCP does not do

MCP does not automatically:

- upload the local database to OpenWorkGraph;
- require OpenWorkGraph cloud storage;
- give an AI unrestricted access to every employee;
- make inferred task labels ground truth;
- expose clipboard contents or typed text;
- bypass endpoint/company sharing policy;
- turn observed repetition into permission or policy.

It is simply a standardized way for an authorized AI client to ask the evidence service for specific context.

## Running the Gateway MCP adapter

The Gateway MCP adapter (`gateway/mcp.py`) is a separate stdio MCP server that calls a running Gateway's REST API with a service token. It is not started by the Gateway Docker image.

```bash
export OWG_GATEWAY_URL="https://gateway.example.internal"
export OWG_GATEWAY_SERVICE_TOKEN="<integration token with evidence:read>"
python -m gateway.mcp
```

Configure your MCP client to launch that command. The adapter only exposes what the token's scopes and actor restriction allow.
