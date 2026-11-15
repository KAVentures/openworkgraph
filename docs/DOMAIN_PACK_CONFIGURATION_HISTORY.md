# Review domain-pack configurations

The domain-pack panel can show retained configurations, including the saved
role mappings, parameters, version, installation date and definitions digest.
Reconfiguration retains up to 20 previous setup snapshots in the existing local
installation record. Uninstall or log deletion removes them with that record.

Older installations stored only version, date and digest. Those entries remain
visible with configuration marked unavailable; their mappings and targets are not
invented. The history is bounded and does not establish a complete installation
history. Snapshots contain no historical event log or verified business outcomes,
and do not restore a configuration or grant execution authority.

The authenticated local dashboard endpoint returns these snapshots with no-store
headers. It preserves the existing owner-session boundary for event-log data.
