# v0.35 regression cases

The automated suite covers these user-observed cases:

- `Koyar Afrasyab - Google Chrome` renders with `OWNER` while the stored event remains unchanged.
- A target label containing only `Koyar` renders as `OWNER`.
- Owner email/phone values can render as `OWNER_EMAIL` / `OWNER_PHONE` when explicitly configured.
- `Select Anna Svensson, Contract renewal with Koyar Afrasyab` in Gmail renders as `Select PERSON_xxxxxx, Contract renewal with OWNER`.
- A high-confidence person learned earlier can be masked when the name later occurs inside a displayed email subject.
- Event IDs, timestamps, counts, durations and activity metrics are preserved.
- Capture/inference modules cannot import the presentation redactor.
- macOS release builder must contain one embedded launcher and use `ditto`.
