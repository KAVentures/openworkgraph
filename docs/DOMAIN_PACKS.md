# Domain packs

A domain pack bundles the definitions a process team would otherwise rebuild for every log of a well-known
process. Three packs ship, each at version 1.0.0: **order to cash** (`order_to_cash`), **procure to pay**
(`procure_to_pay`) and **IT service management** (`service_management`). Packs are local definitions: they add no source access, uploads or model calls. Both can be
installed on the same log; each owns its own metrics.

## What a pack contains

- **Roles:** what the process's object types and activities mean, for example `order`, `item`, `delivery`,
  `invoice` and `payment`, or `create_order`, `ship` and `receive_payment`. Each role is required or optional.
  The pack maps them to the names your log uses, because no two source systems name them alike.
- **Case metrics** for the [typed analysis](EVENT_LOG_WORKSPACE.md):
  - order to invoice and invoice to payment time;
  - order changes per order;
  - item pick-to-ship time.
- **Compliance rules** per object type:
  - every invoice follows its order, within *invoice within (days)*;
  - credit approval by someone other than the order's creator;
  - picking before shipping;
  - every invoice paid, within *payment terms (days)*.
- **Object rules** across types:
  - one delivery per shipping event;
  - shipping only after every carried item is picked;
  - every order has items;
  - one order per invoice.
- **A reference object model:** a lifecycle per object type with single or variable arcs. It is checked like any
  [object-centric process model](OBJECT_CENTRIC_PROCESS_MODELS.md).
- **Parameters:** values with stated defaults: invoicing within 14 days of the order, and payment terms of 30
  days. They are not your policy until you set them.
- **Assumptions:** for example, each order is invoiced once, and a payment may settle several invoices.

## Procure to pay

- **Roles:**
  - object types: purchase requisition and goods receipt (optional), purchase order (required), vendor invoice
    (required) and payment (optional);
  - activities: create and approve requisition, create, approve and change the order, receive goods, post and pay
    the invoice.
- **Case metrics:**
  - requisition to order;
  - order to goods receipt;
  - order changes;
  - invoice to payment.
- **Compliance rules:**
  - three-way match: every invoice of an order comes after its goods receipt;
  - goods received within *delivery within (days)* of the order;
  - order approval by someone other than its buyer;
  - goods received only for approved orders;
  - orders only from approved requisitions;
  - invoices paid within *payment terms (days)*;
  - every invoice paid exactly once, which flags duplicate payments.
- **Object rules:**
  - every invoice posting involves a purchase order, so invoices without one are flagged;
  - one order per goods receipt;
  - an invoice is posted only after the goods receipt of every order it covers.
- **Reference model:** lifecycles for requisitions, orders, goods receipts, invoices and payments, with
  single and variable arcs. An invoice may cover several orders, and a payment several invoices.
- **Limits:**
  - Three-way match compares the order of events only, not amounts or quantities.
  - Partial deliveries and partial invoices show as deviations.
  - The procure-to-pay fixture (`tests/procure_to_pay_fixture.py`) checks all of the above against planted
    deviations, counted by hand:
    - three invoices posted before their goods receipt;
    - five orders received without approval;
    - four orders from unapproved requisitions;
    - three late deliveries;
    - one invoice never paid and one paid twice;
    - one invoice without any order.

## IT service management

- **Single-case CSV:** this pack works on a single-case CSV export of incidents: map the incident role to the
  log's `case` object type. Problems are optional, for object-centric logs.
- **Roles:** incident (required), problem; log incident, assign, start work, reassign, resolve, reopen, close.
- **Case metrics:**
  - time to assign;
  - time to the last resolution;
  - reassignments per incident;
  - reopenings per incident.
- **Compliance rules:**
  - assigned within *response hours* (default 4);
  - resolved within *resolution hours* (default 72);
  - resolved only after assignment;
  - at most two reassignments (more counts as ping-pong between groups);
  - never reopened;
  - closed only after resolution.
- **Parameters:** these are in hours. Parameters declare their unit, days unless stated, so the other packs'
  definitions are unchanged.
- **Object rules:** one incident per resolution, and every problem has incidents (when problems are mapped).
- **Reference model:** the happy path (log, assign, optionally start work, resolve, optionally close).
  Reassignments and reopenings deviate from it by design, and are counted by their own rules.
- **Limits:** times are elapsed. Time on hold, with the requester or outside support hours is not excluded.
- **Fixture:** `tests/service_management_fixture.py` is a 60-incident CSV. The report finds every planted
  deviation and fits the happy path to the 40 incidents without one:
  - six late assignments and three late resolutions;
  - four ping-pong incidents;
  - seven reopened incidents;
  - one resolved before assignment and one closed before resolution.

## API

| Route | Purpose |
| --- | --- |
| `GET /v1/event-log-domain-packs` | Packs, versions, roles, parameters, assumptions and definition counts. |
| `GET /v1/event-log-domain-packs/{pack_id}?version=` | The pack as published, for review before installing. |
| `GET /v1/event-logs/{log_id}/domain-packs` | Installs on the log, whether an upgrade is available, and the log's object types and activities to map (most frequent 1,000 each). |
| `POST /v1/event-logs/{log_id}/domain-packs/{pack_id}` | Install, upgrade or reconfigure: `{"version", "mapping": {"object_types": {role: name}, "activities": {role: name}}, "parameters": {...}}`. |
| `DELETE /v1/event-logs/{log_id}/domain-packs/{pack_id}` | Uninstall, removing the pack's case metrics. |
| `GET /v1/event-logs/{log_id}/domain-packs/{pack_id}/report` | Run every resolved definition on the log. |

All routes need a dashboard session, return `no-store` and refuse repeated JSON keys.

## Install, upgrade and uninstall

- **Install:** an install records the pack version, mapping and parameters, plus a SHA-256 digest of the
  resolved definitions. It saves the pack's case metrics to the log's metric library, so they can be reused in
  analyses; the install owns them.
- **Upgrade and reconfigure:** installing a newer version upgrades and keeps a history of earlier installs.
  Installing the same version again reconfigures it. A downgrade is refused.
- **Validation:** every required role must be mapped to a name that exists in the log, and no two roles may share
  a name. Parameters must stay within their stated range.
- **Your own metrics:** a saved metric of yours with a pack metric's name is never overwritten; the install is
  refused instead.
- **Uninstall and deletion:** uninstalling removes only the install and its own metrics. Deleting the log deletes
  its installs.

## Report

The report runs the resolved definitions through the existing engines:
- typed case metrics, with coverage;
- compliance rules per object type;
- object rules;
- object-centric conformance of the reference model.

Definitions that use an optional role you did not map are listed in `not_applicable`, never silently dropped.
The report refuses to run if the pack's definitions for the installed version no longer match the recorded
digest.

## Limits and interpretation

- **Not validated against a source system:** the pack states assumptions about a typical order-to-cash process.
  It is not validated against any particular ERP (`source_system_validated: false`). A mapping is your assertion
  that the log's names mean the pack's roles.
- **Segregation of duties** needs recorded resources. OCEL imports keep none, so those cases are reported as
  unknown, never as satisfied.
- **What results mean:** results describe the log, not verified business outcomes, policy compliance or the
  source system's completeness.

## Dashboard

The **Domain packs** section of a saved log:
1. **Choose:** pick a pack. An installed pack is selected first.
2. **Map:** maps each role to the log's object types and activities. Names equal to a role label are suggested,
   and an existing install's mapping is kept.
3. **Parameters:** set the parameter values.
4. **Act:** install, run the report or uninstall.

## Validation

- **Fixture:** a deterministic synthetic log (`tests/order_to_cash_fixture.py`) uses SAP-like names, so no
  name equals a role label. It has 20 orders with known deviations:
  - two orders invoiced after 20 days;
  - two items shipped unpicked;
  - one invoice never paid.

  The report finds each one. The object model fits 115 of 118 objects.
- **Behaviour checks:**
  - unmapped optional roles are listed as not applicable;
  - parameters change the resolved rules;
  - upgrades keep history and replace the pack's own metrics, and a downgrade is refused;
  - a user's own metric is protected;
  - a changed definition under the same version is detected;
  - deleting the log removes its installs;
  - ten invalid installs are refused.
- **Stability:** a test pins the order-to-cash digest, so adding a pack never invalidates existing installs.
- **Routes:** checked over real HTTP.
- **Browser:** `tests/browser/domain_packs.cjs` maps, installs, reports, changes a parameter, reinstalls and
  uninstalls, and checks WCAG 2.1 AA at 390 px width.
