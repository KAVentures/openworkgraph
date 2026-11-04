# Offering Open Work cases to agents

Open Work can hand individual cases to connected AI agents without giving them
any new permission.

1. In **Privacy → Open Work**, tick **Let connected agents pick up cases I offer**.
   It is off by default. Turning it off withdraws every offer at once.
2. In the saved review list, click **Offer to agents** on a case. Only cases
   already in the local review queue can be offered. An offer lasts up to the
   review's own expiry, or at most seven days.
3. Agents use three MCP tools, which are exposed only while **Experimental Open
   Work AI tools** is on and AI access is allowed:
   - `list_offered_work`: offered cases with their opaque `owg:w:` reference,
     the owner's review intent and optional note;
   - `claim_offered_work`: takes a case under a lease (30 minutes by default,
     5–240 configurable) and returns a `lease_token`. Only one agent can hold
     a case. An expired lease returns it to the pool;
   - `report_offered_work`: `attempted`, `declined` or `needs_human`, with an
     optional note and an opaque `operation_receipt_ref`. A decline returns the
     case to the pool; the other outcomes wait for the owner.
4. The owner reviews the report, then re-offers or closes the case. Completion
   is established only by the independent `verify_case_operation` source check.

Boundaries:

- Agents act only through their own, separately authorized business tools. An
  offer is a request for help, not a permission, obligation or verified duty.
- Offers contain no provider IDs, customer data or credentials. The queue is a
  local `0600` file in the auth folder and is never synced.
- Agent reports are stored as unverified claims (`verified: false`).
- Choosing session-only history clears the queue together with saved reviews.

REST: `GET /v1/open-work/dispatch`, `POST /v1/open-work/dispatch/settings`,
`POST /v1/open-work/dispatch/offers` (`confirm_offer: true`),
`.../offers/withdraw` and `.../offers/acknowledge` need a dashboard session.
Agents use `/v1/open-work/dispatch/agent` and its `claim`, `release` and
`report` endpoints.
