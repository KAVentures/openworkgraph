# Renewing a retained Jira profile’s credential

A retained Jira dataset can continue after its configured OAuth access token changes, provided the current credential authenticates the **same previously verified account and tenant**. This is an explicit manual account check. It does not read issues or histories, refresh an OAuth token, enroll an account, or establish source completeness.

An administrator updates `OWG_JIRA_OAUTH_ACCESS_TOKEN` using the existing deployment configuration and restarts the service when changing its process environment. Keep the existing host, cloud ID, local HMAC key, selected references/issue keys and source-read opt-ins. Never enter a token in a dashboard field, agent argument, URL or issue comment. Native issue-key profiles still require their separate native-ingestion opt-in.

In **Retained Jira histories**, refresh profile status and choose **Verify renewed credential**. Review its confirmation. The check is available only after the original credential has successfully retained a dataset and established an account binding. It also works while the profile is paused: checking account permissions does not enable history ingestion. Afterwards, explicitly enable and run the profile if you intend to read histories again.

The check validates current site read grants and the authenticated active account twice, using only the fixed accessible-resources and current-user endpoints. It has a 20-second overall budget within the existing request limits and profile lease. It compares the account with the stored local HMAC binding; raw account IDs and tokens are not returned or retained in profile metadata. Provider revocation is checked during these reads, not claimed to be atomic with a later local commit.

Success changes only credential-continuity metadata and its configuration binding. Dataset ID, source namespace, selected cases, event manifest, checkpoint, last history attempt/success, existing history errors and paused/enabled setting are preserved. The current analysis and unsaved analysis inputs stay open. A separate credential status and verification time cannot make old history appear freshly ingested. Repeating a check with an unchanged credential does not increase its credential generation.

A different account/tenant, missing original account binding, deleted dataset, changed selection or lost local HMAC key cannot adopt old history. A failed check preserves the prior credential binding and dataset. A provider backoff also blocks immediate retries. Keep or disconnect the old profile and explicitly create a separate profile when a different identity is intended; logs from separate profiles are not automatically merged.

Each check requires the current profile revision, increments that revision, and replaces the run lease. This invalidates an earlier in-flight history read. Disable, enable, disconnect, dataset deletion, local configuration/scope changes and replacement of an expired check invalidate old workers. Lifecycle controls remain available during the request, and held old responses cannot restore an enabled dashboard state. Transaction failure leaves the old binding; after a lost response, refresh status before retrying. Existing history events are never written by this operation.

The local dashboard-session-only API is:

```http
POST /v1/event-log-sources/jira/{profile_id}/credential
Content-Type: application/json

{"revision": 3, "confirm_account_check": true}
```

Both fields are required; revision is a positive integer and confirmation is literal `true`. Additional fields, credential values, duplicate JSON keys and nonfinite JSON values are refused. It is not exposed as an agent execution capability.

Owned fake-provider, real local HTTP and browser tests cover credential continuity, replay, account/grant refusal, pause preservation, concurrent lifecycle changes, rollback/lost acknowledgements, stale responses and secret exclusion. They do not certify live Jira OAuth compatibility or customer acceptance. OAuth enrollment, refresh-token exchange, a credential vault, tenant scheduling and wider connector rollout remain outstanding under #436/#431/#370.
