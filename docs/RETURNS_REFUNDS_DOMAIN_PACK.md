# Returns and refunds process pack (R23)

`returns_refunds` v1.0.0 extends OWG's **local, optional** domain-pack catalog. It works with a CSV log whose case column identifies an individual return request, or with an OCEL log in which return objects and event relationships have been explicitly mapped. Nothing is fetched from a merchant, refund processor or payment provider.

## What it measures

The human maps source activity labels to the named roles in the domain-pack setup. Required: return case, request, inspection. Optional: receipt, approval, rejection, refund, closure, refund object and returned item. The pack creates five reusable case metrics (request-to-inspection time, approval-to-refund time, request-to-refund time, refund-event count, rejection-event count) when the relevant roles are mapped.

Review checks describe recorded order and counts: request before inspection, inspection before approval/rejection, approval before refund, refund before closure and multiple refund events. The default review target is **7 elapsed days**; the default approved-to-refund target is **14 elapsed days**. Both are explicit adjustable modeling assumptions, not adopted policy. An approval-to-refund deadline is only checked where **approval was recorded**; rejection without approval is not a refund obligation.

For OCEL, the optional item and refund objects enable additional relationship and lifecycle checks. An imported single-case CSV has only its case object and cannot provide those additional relationships.

## Example

Import a source export after confirming that each case ID denotes one return in one source/tenant. In the local Event Logs workspace, select the **Returns and refunds** pack, map case type `case` to `return_case` and the source's actual event names to their matching roles, review the parameters, install, then run the pack report. A saved install pins a digest of all resolved definitions. Reconfiguring, uninstalling and deleting the source log use the existing domain-pack lifecycle and removal rules.

## Interpretation and limits

The reference lifecycle is the common approved-return route, **not a business rule**. Legitimate rejections and partial refunds may appear as deviations. Repeated refund events are only review candidates; they do not prove a double payment. Likewise, a posted refund does not prove actual settlement, monetary correctness or entitlement. A missing record can reflect an open case, partial extract, delayed integration or deletion. The output is descriptive, not a compliance or verified-customer-outcome claim.

The pack makes no new network calls, does not enable capture or sharing, cannot trigger refunds, and does not grant an AI agent or any user permission to write to a provider. Tests use synthetic data only; merchant-source sandbox acceptance remains a separate gate.
