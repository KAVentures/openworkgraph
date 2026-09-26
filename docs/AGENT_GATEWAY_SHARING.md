# Agent evidence and organization Gateway sharing

Agent execution evidence is **local-only by default**, even when organization Gateway synchronization is enabled for other OpenWorkGraph evidence.

The endpoint configuration loader supplies this privacy floor when the setting is omitted:

```json
{
  "gateway": {
    "enabled": true,
    "local_policy": {
      "allow_agent_events": false
    }
  }
}
```

To deliberately share privacy-hardened structural agent evidence with the configured customer-controlled Gateway, the endpoint owner must opt in locally:

```json
{
  "gateway": {
    "enabled": true,
    "local_policy": {
      "allow_agent_events": true
    }
  }
}
```

The effective policy remains restrictive:

- a local `false` cannot be broadened by organization policy;
- an organization policy may set `allow_agent_events: false` to narrow an endpoint opt-in;
- normal desktop/browser evidence keeps its existing Gateway policy behavior;
- this setting changes synchronization only. It does not disable local agent capture, local agent reports, exports, REST, or MCP access.

Agent evidence sent after explicit opt-in is still passed through the existing endpoint-side Gateway privacy filtering and identifier sanitization before transmission.
