# Integrating with OpenWorkGraph

OpenWorkGraph is intended to be an upstream work-evidence/context layer for automation systems, company-context systems, internal agents, and other AI platforms. Integrations should consume observed evidence rather than assume OpenWorkGraph's current inferred task labels are complete.

## Recommended integration hierarchy

1. **REST** for a backend/service integration.
2. **MCP** for an AI/agent client that wants tools during reasoning.
3. **Export** for manual/ad-hoc analysis.

All should ultimately refer back to the same canonical rich evidence events.

## REST quick start

Create one organization-scoped service credential per integration, granting only required scopes. Then call the customer-controlled Gateway:

```http
GET /v1/workflow-trace?limit=100
Authorization: Bearer <service token>
```

The response is chronological and paginated:

```json
{
  "rows": [
    {
      "event_id": "...",
      "observed_at": "...",
      "app": "...",
      "event_type": "...",
      "metadata": {},
      "clipboard_transfer_id": "..."
    }
  ],
  "has_more": true,
  "next_cursor": "...",
  "data_layer": "privacy_hardened_raw_rich_evidence",
  "derived_task_inference_authoritative": false
}
```

Pass `next_cursor` back to continue through the same frozen result snapshot.

## Core endpoints

### `/v1/workflow-trace`
Canonical chronological observed evidence. Filters include date range, actor, device, session, event type, and search text.

Use this when fidelity matters or when the consuming AI should reconstruct a workflow itself.

### `/v1/search`
Searches the rich evidence layer. Useful for finding examples around a concept/resource/title before requesting more surrounding evidence.

### `/v1/context/current`
Returns recent observed evidence for an authorized organization/actor/device. It deliberately does not claim an inferred current intent/task.

### `/v1/transfers`
Returns observed copy/cut/paste linkage grouped by transfer ID. Clipboard contents are never returned because OpenWorkGraph does not capture them.

## Example: automation discovery platform

An automation platform can use OpenWorkGraph as the discovery/evidence layer before a deliberate workflow recording or automation build:

```text
continuous work evidence
        |
        v
search/cluster repeated traces
        |
        v
select candidate workflow
        |
        v
fetch several real evidence examples
        |
        v
build/record/test automation
```

OpenWorkGraph can also provide evidence about the human work surrounding exceptions after automation deployment. The integration should keep evidence provenance (`event_id`s) so recommendations remain auditable.

## Example: company-context platform

A company-context or organizational knowledge platform can treat OpenWorkGraph as another organization-owned context source, complementary to documents, email, meetings, CRM, and interviews.

Instead of treating every click as permanent company knowledge, it can query evidence when reasoning about operational questions such as:

- what actually happens during a process;
- which tools are crossed during a workflow;
- where information is transferred;
- what human exception paths recur;
- how several real executions differ.

The consuming platform can perform its own AI interpretation over OpenWorkGraph evidence.

## Evidence provenance

Prefer outputs that retain links back to source event IDs:

```text
AI conclusion / candidate workflow
        |
        +--> evidence event IDs
        +--> time window
        +--> actor/team scope if authorized
```

This allows a human or another model to verify the conclusion against the observations rather than trusting an opaque task classifier.

## Do not treat heuristic views as truth

OpenWorkGraph may expose task/process summaries for convenience, but integrations should treat them as indexes or candidate interpretations. They can be regenerated as models improve.

The durable value is the privacy-hardened evidence history.

## Security rules for integrations

- Use a dedicated service credential per integration.
- Grant the minimum scopes required.
- Never embed Gateway admin/enrollment secrets into an integration.
- Store the returned service token in the integration's secret manager.
- Revoke the service token when the integration is removed.
- Use TLS for networked production Gateways.
- Keep the Gateway inside the customer's network/VPC unless the customer deliberately exposes it through a protected access edge.
- Do not log raw evidence indiscriminately in integration logs.

## MCP integrations

See [MCP architecture](MCP_ARCHITECTURE.md). MCP is an alternative interface over the same evidence service, not a requirement and not a cloud-storage mechanism.
