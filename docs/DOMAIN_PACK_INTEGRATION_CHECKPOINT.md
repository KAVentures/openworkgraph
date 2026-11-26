# R23 integrated domain-pack checkpoint

Combines #1518–#1523 against the same reviewed base. Retains the new industry packs, portable setup, deadline comparison, portfolio benchmarking, compliance overlap and weighted priority worklists, including their tests and documentation. Shared dashboard and route insertions are combined; browser scenarios run portable setup, deadlines, overlap and priority before the portfolio scenario, which changes log selection. Original PRs are preserved until integration CI succeeds. Do not merge both this candidate and the individual overlapping PRs. No competitor parity or real-customer acceptance is claimed.

Successful pack installation, reinstallation or removal reconstructs the analysis panels, clearing old selections and results and preventing detached views from accepting in-flight responses. This is UI invalidation in addition to the API's exact basis checks.

## Event-linked case coverage

Deadline, overlap and priority reviews now pin and display the count of retained objects, event-linked projected cases, and objects excluded because they have no linked event, per evaluated object type. Eventless objects are not counted as satisfied or nonactivated; this follows the established compliance engine projection. Review computation refuses mismatched denominators. Complete worklist exports retain the same coverage metadata. Neither event linkage nor complete evaluation of the projection establishes completeness of the upstream source.
