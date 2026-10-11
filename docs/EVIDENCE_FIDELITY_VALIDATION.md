# Evidence fidelity fixes and verification

Baseline: `68c561478e9e2616ba1c9146af82d45809f14e4a`. Verified in a disposable Linux
source environment on 2026-10-11; no production capture/text consent was enabled.

| Observed problem | Change | Verification |
| --- | --- | --- |
| Evidence UUIDs redacted inside lists | Preserve bounded structural reference arrays | UUID/reference regression in compact and rich projection |
| Hosted clipboard anchors dropped | Retain transfer/copy/source linkage, including false contents-captured flag | Projection regression; authenticated 54-case replay |
| Numeric search matched hidden metadata/IDs or partial PR numbers | Validate candidates against hosted visible fields; explain matched fields; exact resource-reference lookup; bounded scans and search cursors | OAuth numeric-search regression; 2,002-row bounded scan/pagination regression |
| Current context lacked visibility diagnostics | Separate observation and receive times; report unknown local recorder state, text visibility and server catalog version | OAuth context tests |
| Same-host text snapshots lacked page provenance | Pairing/installation-keyed page identity; separately gated production resource reference; context allowlist | Local gate/dedup tests; capture-to-sync-to-OAuth-excerpt test |
| Bounded excerpts looked complete | Viewport-first bounded capture; capture/sync/presentation truncation and character counts | Actual browser script VM tests, including visible tail after long old DOM prefix |
| Text approval/deletion had no account screen | PKCE account screen, explicit device selection and acknowledgement, account-wide deletion, read-only scoped status API | Browser UI execution tests; signed OAuth/actor isolation/CSP tests |
| Fixture format drifted from capture contracts | Production formatter, valid references/hosts, conservative confidence and canonical chronology | All 54 fixtures normalizer/formatter checked and authenticated MCP replayed |
| Scoring conflated fragmentation with contamination and rewarded certainty about hidden intent | Separate fragmentation/mixing/unknown IDs; dimensioned uncertainty; ambiguous partition scores diagnostic; identifiable membership gate | Oracle, naive baselines, hallucinated-ID, fragmentation, ambiguity and outcome regressions |
| Human execution windows looked like verified task membership | Explicit time/session-window provenance and unrelated-event warnings; structural candidate basis | Existing workflow evidence regressions plus full suite |

Verification completed:

- Full Python suite twice: first 1,385 passed / 1 skipped, then 1,392 passed / 1 skipped.
  The existing skip is the MCP usability probe when port 8788 is occupied.
- Final targeted hosted OAuth/fidelity run: 61 passed after search cursor/reference changes.
- Final full browser suite: 107 passed, including executable capture and account UI tests.
- Architecture budgets: 31 import registrars / 18 dashboard rewrites / 5 route replacements,
  all unchanged; dependency lock, injected dashboard syntax and diff whitespace checks passed.
- Existing deprecation warnings concern FastAPI startup handlers; no new dependency was added.

Initial full-suite failures came from an environment SOCKS proxy intercepting loopback
requests. Both lowercase and uppercase proxy variables were cleared for local transport tests;
no authorization or privacy assertion was disabled.

The PostgreSQL smoke script now verifies provenance persistence/reference lookup and repeated
schema initialization alongside its existing actor-lock/quota/deletion checks. A local real
PostgreSQL attempt was blocked: this container cannot create/switch to a non-root database
user. PostgreSQL and macOS/Windows results must come from repository CI and are not claimed here.

Production limits:

- Changes require deployment and client catalog refresh. Existing sessions may still expose
  the old seven-tool catalog even when the server has ten tools.
- The account screen's issuer registration, HTTPS callback and browser token-exchange CORS
  must be validated on a deployment. Mock browser and signed transport tests cannot prove
  provider configuration. Cloud text stays behind the existing disabled-by-default operator gate.
- No new independent model efficacy result or verified real-work study was produced. Synthetic
  fidelity/regression tests establish mechanics, not user value. Old benchmark scores are not
  directly comparable to the version-2 observability contract.
