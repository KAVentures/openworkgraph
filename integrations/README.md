# OpenWorkGraph integrations

This directory is the map for software that connects **to** OpenWorkGraph. Some compatibility/published packages still live at the repository root for now; this page is the single navigation surface while those paths are migrated safely.

## Agent integrations

- [../adapters/](../adapters/) — Python adapters for agent runtimes and execution/approval/context hooks.
- [../plugins/openworkgraph/](../plugins/openworkgraph/) — installable agent/plugin package with commands, hooks and OWG skill.
- [../openworkgraph_agent/](../openworkgraph_agent/) — compatibility Python package.
- [../sdk/](../sdk/) — published Python/TypeScript integration SDKs.

## MCP packaging

- [../mcp_server/](../mcp_server/) — local MCP implementation used by connected AI clients.
- [../mcpb/](../mcpb/) — MCP bundle wrapper/manifest used for packaged client installation.

## Productivity-suite integrations

- [microsoft365/](microsoft365/) — Microsoft 365 Copilot / declarative-agent configuration templates for a customer Gateway.

## Organization connectivity

- [../connector/](../connector/) — endpoint-to-Gateway synchronization and sharing-policy enforcement.
- [../gateway/](../gateway/) — optional customer-controlled organization evidence service and remote MCP/REST surface.

## Compatibility rule

Do not move a published package, installer path, client manifest, or import path merely to make the repository tree prettier. First move internal consumers to the canonical location, preserve a compatibility path where external users may depend on it, and require the full Linux/macOS/Windows + packaging + installer smoke matrix before removing the old path.

The long-term target is to consolidate these surfaces under a smaller number of top-level directories without changing user-facing install or integration contracts.
