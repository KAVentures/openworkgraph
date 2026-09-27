# OpenWorkGraph v0.95.0

## Custom agents and harnesses

OpenWorkGraph now exposes a first-class, framework-neutral path for arbitrary agent runtimes. A self-built harness, an OpenClaw/Hermes-style setup, an internal company agent, or another framework can participate without OpenWorkGraph needing a named native adapter.

The connection is deliberately two-way and independent:

- **agent -> OpenWorkGraph:** privacy-safe structural execution telemetry through the existing write-only agent-ingest boundary;
- **OpenWorkGraph -> agent:** optional authorized context over the compact local MCP surface.

Giving a harness the telemetry token never gives it context/history read access. Giving a harness MCP context never silently enables observation of its execution.

## Standalone helpers

v0.95 adds:

- a dependency-free Python helper (`openworkgraph-agent`) with run/model/tool context managers;
- a dependency-free Node 18+/TypeScript helper with declarations;
- existing OTLP/HTTP JSON integration for harnesses that already emit portable GenAI traces;
- raw structural HTTP for any other language/runtime.

The GitHub release publishes the Python and Node helper files as standalone assets in addition to the normal macOS, Windows and Claude MCP packages.

## Privacy boundary

The custom helpers intentionally have no API for prompt text, model-response content, tool arguments/results, returned values, exception text or hidden reasoning. Tests verify that returned secrets and exception messages remain in the harness and never enter telemetry.

Observation failures remain fail-open for the agent: bounded background delivery can drop telemetry, but it does not enter the agent's control path.

## Reliability

- Python telemetry admission and shutdown share a synchronization boundary, preventing events from being accepted after the worker has closed.
- Node shutdown waits for an already in-flight telemetry request before returning.
- The local dashboard setup endpoint is authenticated and non-cacheable.
- The generated setup keeps write-only telemetry credentials separate from MCP/history permissions.

See [Custom agent harnesses](CUSTOM_HARNESSES.md) for the integration model and examples.
