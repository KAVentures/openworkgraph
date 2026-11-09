# Grouping activities (event abstraction, R9)

Logs exported from source systems often record work at a finer grain than the process you
want to see: *Type name* and *Type address* are both data entry; four different approval
codes are all "Approve". **Group activities** in the event-log workspace lets you name those
groups once and see every analysis at the higher level.

## Mappings

A mapping has a name and one or more groups. Each group is a label and the original
activities it stands for. An activity can belong to only one group, and activities in no
group keep their own name. Optionally, **collapse consecutive repeats into the first**:
within a case, a run of events that map to the same label becomes its first event, so
*Enter data → Enter data* reads as one step.

Activities that are not in the log are accepted but listed back, so typos are visible. A log
keeps at most 50 mappings, and deleting the log deletes them.

## Applying a mapping

Saving a mapping does not change anything. **Apply to analyses** applies it, and at most one
mapping is applied per log. While it is applied, every case-centric analysis (overview,
variants, process maps, KPIs, case filters, conformance, predictions, simulation and the
rest) reads the mapped activities. Every result then includes `activity_mapping`, giving
the mapping's name, ID and SHA-256 digest of its definition, how many events were relabelled
and how many repeats were collapsed. The dashboard shows a notice under *Overview*. **Stop
applying** returns to the original activities.

The stored events are never changed (`original_events_unchanged: true`). Saved signal rules
and object-centric views keep the original activities, so a rule written for *Type name*
keeps meaning *Type name*.

## API (dashboard session only)

| Route | Purpose |
|---|---|
| `GET /v1/event-logs/{log_id}/activity-mappings` | Saved mappings and the original activities with event counts |
| `POST /v1/event-logs/{log_id}/activity-mappings` | Save a mapping: `{"name", "groups": [{"label", "activities": [...]}], "collapse_repeats"}` |
| `POST /v1/event-logs/{log_id}/activity-mappings/applied` | `{"mapping_id": "amap_…"}` applies one; `{"mapping_id": null}` removes it |
| `POST /v1/event-logs/{log_id}/activity-mappings/{mapping_id}/delete` | Delete a mapping |
