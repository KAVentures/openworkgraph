# Private source-host crosswalks and versioned case reviews

Part of enterprise delivery #1572. Extends #1582's explicit case review
and the exact publication system #1578. Crosswalks accelerate human-reviewed
linkage across native connectors; they are not independently verified
entity resolution. An operator must obtain the cross-reference from
authorized business records or another approved mapping source.

## Prepare a crosswalk on each source host

Supported case resolvers:

| Provider | native_case_id | native_type |
|---|---|---|
| Customer REST | exact mapped case string | omit |
| SQL | exact canonical mapped case string (including integer/UUID as string) | omit |
| Salesforce | parent CRM record ID | selected history object, e.g. CaseHistory |
| ServiceNow | exact sys_id | selected table, e.g. incident |
| SAP procurement | purchase order number | purchase_order or omit |

Google Workspace and Slack actor/thread/channel cases do not currently
have a general business-record crosswalk resolver; use explicit review
instead. The resolver applies the exact adapter's case HMAC and publication
HMAC, then requires the resulting case to occur in the selected snapshot.
No source API queries, raw-ID disclosure, guessing, fuzzy matching or
provider relationship discovery take place.

First prepare and publish the exact source snapshot through the existing
connector publication workflow. On each host, obtain an authorized mapping
in a private 0600 JSON file, for example:

    {
      "rows": [
        {
          "native_case_id": "4500001234",
          "native_type": "purchase_order",
          "business_key": "approved-common-reference-123"
        }
      ],
      "basis": "reviewed_native_reference",
      "confirm_reviewed_mapping": true,
      "confirm_match_tag_disclosure": true
    }

This is a fictional example. For a different connector, use its native
case type and exact identifier. The common business key must have the
same exact Unicode representation across all participating hosts.
Normalization rules are an operator responsibility; the tool deliberately
does not fold case, trim, perform Unicode normalization or infer equivalent
keys. Leading/trailing whitespace is rejected.

Generate a dedicated high-entropy 32-byte random secret (64 lowercase
hexadecimal characters) and a separate random 16-byte batch ID (32 lowercase
hexadecimal characters). Provision the same secret to each participating
source host through its protected environment, OWG_CROSSWALK_SHARED_KEY.
Do not transmit it to the Gateway or put it in command arguments, the
input file, artifacts, source control or logs. Use a fresh secret and batch
for each independent review; share only with the approved hosts. Choose
one explicit business-key domain, e.g. purchase_order or support_case.

Run on each host:

    python -m server.enterprise_connector_crosswalk \
      --snapshot /private/approved-snapshot.json \
      --input /private/reviewed-native-mapping.json \
      --out /private/minimized-crosswalk.json \
      --job cj_... --grant cep_... --publication-revision 1 \
      --batch <32-hex-character-batch-id> --domain purchase_order

The CLI re-reads the exact retained snapshot and profile pin. It refuses
missing cases, duplicate canonical native IDs, changed source state, unsupported
case types and unapproved mapping/disclosure. It creates a new 0600 artifact
without overwriting an existing file and prints only counts and artifact
digest. The output includes exact job/grant/manifest pins, batch and domain,
a keyed key-consistency check, case pseudonyms and batch/domain-separated
HMAC match tags. It includes no native case ID, business key or secret.
Do not upload the input or snapshot file in place of the crosswalk artifact.
Match tags and case pseudonyms are still sensitive correlation data.

## Gateway proposal review

Open Gateway administration → Connector event integration → Private
crosswalk proposals and saved reviews. Select two to ten minimized artifact
files, review their exact pins, and separately approve source inspection and
temporary tag processing. The owner-only API checks artifact digests,
common key-check/batch/domain, current publication grants and exact case
membership inside the existing organization transaction.

Matching tags yield candidate groups. Single-source tags remain unlinked;
groups containing multiple cases from one source are withheld unless the
operator explicitly requests ambiguous candidates. No implicit transitive
closure is applied, and each case appears in only one candidate group.
The weakest declared evidence category is propagated to each group.
Candidate evidence digests bind the scope, memberships and contributing
artifact digests. These are provenance fingerprints, not digital signatures
or proof that the submitted native mapping is true. A malicious authorized
owner or source host could submit incorrect assertions. The Gateway does
not independently authenticate which host produced the file.

Every group is unchecked by default. Review original authorized business
evidence and choose groups individually or explicitly select all displayed
groups. Many-to-one groups require a second acknowledgement in the final
plan. Review the exact impact and digest using the existing case-review
engine. Match tags are not returned in proposal output or persisted by the
Gateway application; ensure infrastructure request-body logging is disabled.
The browser clears the artifact selection after a successful proposal.

## Save, revise and export

A reviewed plan can be saved only with separate assertion and retention
consent. This stores pseudonymous case memberships, source pins, optional
evidence digests and summary, not event rows or match tags. A plan has a
random ID and monotonically increasing immutable revisions. Save-as-new or
compare-and-swap next revision; stale revisions cannot overwrite a newer
one. Up to 20 active plans per organization and five retained revisions per
plan. The latest revision and all historical versions remain bounded by
their exact underlying grant expiry. Opening recomputes the original plan
against still-current publications. Invalidated historical revisions are
inaccessible; if the head becomes invalid, the entire saved plan is
invalidated. Cleanup runs every minute and on inventory/save/read; expiry
and deleted grants deny reads before cleanup. Deletion removes the plan and
its history, not the underlying publications. Database backups and
previously downloaded copies require separate retention handling.

Reopen a saved revision, edit its candidate selection and preview again,
or save a new reviewed revision against an explicitly selected current
head. Export always requires new consent and exact digest revalidation.
It does not automatically publish a derived dataset. Use a new local
mining log for each changed plan as described in
GATEWAY_REVIEWED_CROSS_SOURCE_CASES.md.

## Validation and boundaries

Tests use actual local retained-log snapshots, owner-scoped Gateway
publication, keyed crosswalk preparation, proposal generation and mining
import. SQLite/PostgreSQL CI includes concurrent revision compare-and-swap,
retention/expiry, cross-tenant isolation, ambiguity, stale grant rejection,
private file permissions and HTTP access controls. Full CI, browser
acceptance, multi-host secret distribution and live customer crosswalk
validation remain required.

Bounds: 2,000 rows per crosswalk, 10,000 rows across an input batch, 3 MiB
request, 20,000 published events, 500 groups/2,000 linked members and
20 members per group. No arbitrary source query, full master-data
management, cryptographically signed provider attestation, semantic
matching, persistent automatic entity graph or automatic crosswalk
discovery. The source mapping is externally supplied and operator reviewed;
crosswalk artifacts demonstrate retained-case membership, not external
business truth.
