# Fleet acceptance checklist

Run this on representative managed Macs and Windows PCs before broad rollout.

- [ ] Signed/notarized macOS package is accepted without Gatekeeper override.
- [ ] Signed Windows installer is accepted without SmartScreen override.
- [ ] Silent MDM install lands in the employee-visible location.
- [ ] Managed enrollment joins the intended employee/organization and no other.
- [ ] Browser sensor is policy-installed and one-click pairing succeeds.
- [ ] Restart/log out/login resumes observation without opening a distracting dashboard.
- [ ] Sleep/wake resumes observation.
- [ ] Closing the browser dashboard does not stop capture.
- [ ] Deliberate Quit stays quit until next deliberate launch/login behavior.
- [ ] Temporary Gateway/network outage does not stop local capture and sync later recovers.
- [ ] Organization Pause keeps the paused interval local and does not backfill it.
- [ ] Upgrade over the previous version preserves local evidence/config.
- [ ] Offboarding revokes device access and open invitations.
- [ ] Endpoint protection/EDR does not quarantine the signed binaries.
- [ ] Chrome/Edge update of the extension keeps the sensor functional.
- [ ] Seven-day pilot has no unexplained capture gaps.

A failed item is a rollout blocker until understood. CI cannot substitute for these physical-device checks.
