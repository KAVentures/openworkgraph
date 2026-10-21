# Shared organization process designs

The human-enterprise Gateway serves **Shared processes** at `/processes`, linked
from its landing page. It connects versioned local process exports to an
organization inventory, ownership, collaborator roles and independent revision
review. It uses the existing append-only workflow knowledge store; it does not
copy employees' evidence into a second process database.

## Publish and review

1. Review and save a design in the local process catalog; export its JSON.
2. Connect a delegated company identity on the Gateway's **Shared processes**
   page. Select the export and preview its baseline, activities and rules.
   Selecting a file sends nothing. Explicitly confirm both design review and
   organization sharing before publishing.
3. Publish a new shared process or save a selected editable process's next
   revision. The Gateway records the authenticated actor as revision author;
   imported owner labels are descriptive text, not access grants.
4. Assign collaborators by stable organization actor ID. A reviewer with the
   appropriate delegated scope can request changes or record design review.
   The author cannot approve their own revision, even with administrator scope.
5. Inspect history and export an exact revision as JSON, BPMN or a plain-text
   process design document. Saving another revision resets current review
   status; old decisions remain attached to the old revision.

Publishing includes all declared model fields, including baseline labels, goal,
rules, opportunity assumptions and source-state expectations. The publisher
must review these for sharing. Raw event rows and local evidence references are
excluded. Imported timestamps, authors, reviews and policy claims are not trusted
or adopted as Gateway attestations. Private local and per-person Gateway
knowledge is neither searched nor automatically published.

## Identity and permissions

Configure existing [human OIDC access](HUMAN_ACCESS.md). The page currently
accepts an access token from that integration; it is an advanced process-team
interface, not a complete browser SSO login flow. Tokens stay in page memory,
are cleared from the input after connection, and are never stored in browser
storage or URLs. Disconnecting, reloading or switching identity clears the page.
Use the deployment's HTTPS endpoint. OIDC-disabled deployments reject data
access; the static shell contains no organization data.

Add explicit entries to `OWG_GATEWAY_OIDC_GROUP_SCOPE_MAP`, for example:

```json
{
  "Process Readers": ["org:process:read"],
  "Process Authors": ["org:process:read", "org:process:write"],
  "Process Reviewers": ["org:process:read", "org:process:review"],
  "Process Administrators": ["org:process:read", "org:process:write", "org:process:review", "org:process:admin"]
}
```

Scopes combine across verified group memberships. Existing evidence scopes and
service/admin tokens do not grant process access. No process scope is granted
by default.

| Scope | Meaning |
| --- | --- |
| `org:process:read` | Read explicitly published designs, history, collaborator actor IDs and review comments throughout the authenticated organization |
| `org:process:write` | Create designs; edit as owner/editor; manage/archive/delete as owner or process administrator |
| `org:process:review` | Submit revision reviews as owner/reviewer or process administrator; author independence still applies |
| `org:process:admin` | Manage any shared process in the same organization and transfer ownership; read/write/review scopes remain separately required |

Collaborator roles do not grant OIDC scopes. All scoped readers can see published
designs: removing a collaborator revokes their editing/review role, not their
organization reading scope. An owner/editor needs write scope; a reviewer needs
review scope. Ownership transfers require process-administrator permission.
Every request rechecks current roles; a stale open page cannot retain revoked
permissions. IdP group changes take effect when the verifier observes updated
claims, subject to the existing token lifetime.

## Consistency, review and lifecycle

Graph revisions, authorship, access changes and review decisions use the existing
Gateway database. Mutations lock the process metadata, enforce expected model,
access or per-reviewer versions, and commit their audit record in the same
transaction. A failed audit rolls back the mutation. SQLite and PostgreSQL are
supported and tested. No separate persistence service is required.

Reviews pin the current revision and its exact stored content hash. Each
reviewer's latest decision for that revision counts; any outstanding request for
changes takes precedence. Reviewer summaries are bounded to 100 with a truncation
flag; status counts cover all reviewers. Reviews are historical statements by
then-authorized actors. Removing a reviewer does not erase their previous
statements or imply they have current authority. Review status is not business
policy approval, execution authorization or proof of business outcomes.

Archives remain readable and exportable but block edits and reviews. Restoring
is explicit. Deleting requires current model and access versions and removes
all shared revisions, collaborators, authorship and reviews for that process.
Minimal audit records remain; downloaded copies are independent. Private
knowledge is untouched. These are explicitly saved designs, separate from raw
evidence retention settings; apply the organization's model retention procedure
when archiving/deleting them.

## API and bounds

Authenticated endpoints under `/v1/human/processes` provide inventory and
publication. `/{id}` reads or updates a design; `/access`, `/reviews`, `/archive`
and `/forget` make explicitly confirmed changes; `/history` pages revisions;
`/export?revision=N&format=json|bpmn|pdd` exports the exact saved revision.
Inventory defaults to 20 processes and is bounded to 100 per page. History is
bounded to 100 per page. Existing graph limits apply; JSON mutation bodies are
bounded to 256 KiB and reject duplicate keys, nonstandard constants and unknown
fields. Up to 50 explicit collaborators may be assigned. Responses are no-store.

Read audits omit model bodies and review comments. The portal uses nonce-based
scripts/styles and text nodes for model/review rendering. It performs no source
connector reads, agent execution, business writes or automatic publication.

## Verification

`python -m pytest -q tests/test_process_collaboration.py` covers tenant isolation,
private/shared separation, scopes and roles, self-review rejection, stale
revisions, revocation, ownership transfer, archive/deletion and audit rollback.
Set `OWG_PROCESS_TEST_DATABASE_URL` to a **disposable local PostgreSQL database**
to run the same store tests there too. Each test creates and drops its own random
schema; the test refuses remote database hosts.

The CI process-collaboration job also runs the real Chromium acceptance flow
against an isolated Gateway and temporary SQLite database using the existing
injected synthetic identity verifier. It exercises publication, independent
review, a new revision invalidating cached review, immediate reviewer revocation,
read-only historical export, identity switching, mobile layout and memory-only
credentials. No real IdP, customer evidence or live business integration is
certified by these tests.
