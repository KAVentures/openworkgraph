# Sales lead qualification domain pack

The **Sales lead qualification** pack gives a process analyst a starting point
for a selected event log covering lead creation, qualification and opportunity
handoff. It is available in the existing **Domain packs** section after a log is
imported and analysed. Choose the pack, map each required role to an activity or
case type present in that log, review its assumptions, set the two targets, and
select **Install**. The report and its case metrics use that log's normal local
storage, authorization and deletion rules.

The supplied version 1.0.0 defines:

- time from lead creation to qualification, and from qualification to the
  opportunity handoff; optional counts for review and reopen activities;
- declared ordering checks and configurable qualification/handoff response
  targets, initially 48 and 24 elapsed hours;
- a simple successful-path reference lifecycle and a check for more than one
  opportunity-creation event in each selected lead case.

The defaults are illustrative, not a verified sales policy. An analyst must map
roles and set targets appropriate to the log. Unmapped optional roles are
reported as not applicable. Existing pack reconfiguration/version history,
name-collision refusal and uninstall behavior apply unchanged.

Only select a case population for which the assumed successful path is useful.
Open, rejected, disqualified or incompletely observed leads can appear as missing
responses or reference deviations. A flagged case is a prompt for review, not a
verified missed obligation, customer outcome or business-policy breach. The
event-count check does not establish how many distinct CRM opportunities exist:
repeated events may describe the same opportunity or a legitimate process.
Repeated reviews and reopen events can be deviations from the simple reference
path, which allows at most one review and does not model reopening. Time metrics
compare the first recorded occurrence of each mapped activity; response rules
check every initiating event. Elapsed time includes waiting and nonworking hours;
it is not employee effort or a conversion rate.

The pack does not discover CRM records, join identities across systems, import
customer data, verify source completeness, calculate revenue or grant an agent
permission to act. Avoid importing contact details, free text or sensitive
identity evidence unless separately needed and authorized. Real provider/source
validation and review with a customer-specific process remain separate work.
