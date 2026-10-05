# OpenWorkGraph Enterprise Deployment Kit

This directory turns the existing customer-hosted Gateway and managed endpoint support into a repeatable pilot rollout.

## Supported deployment shape

- macOS: deploy the normal OpenWorkGraph `.pkg` to `/Applications` with Jamf or another MDM, then deploy a per-device managed enrollment file.
- Windows: deploy **OpenWorkGraph-Windows-Enterprise-Setup.exe** machine-wide with Intune/SYSTEM or another endpoint manager. Immutable runtime code lives under Program Files; each employee's mutable OpenWorkGraph state stays in LocalAppData. The ordinary per-user Setup EXE remains available for no-admin pilots.
- Gateway: self-host `deploy/docker-compose.yml` + PostgreSQL behind customer-controlled HTTPS.
- Browser sensor: publish the release-built browser ZIP to the Chrome/Edge store your organization uses, then substitute its real store extension ID into the included policy templates.

## The 10-minute pilot path

1. Deploy the Gateway using `docs/SELF_HOSTING.md`.
2. Create employees and personal invitations in `/admin`.
3. Use a **personal/single-use code per endpoint**. Do not place a reusable group enrollment code in a broadly readable MDM profile.
4. macOS: use `macos/install-managed.sh` from Jamf/root context.
5. Windows: use `windows/install-managed.ps1` from Intune **user** context.
6. Confirm the endpoint appears in Gateway Admin and that the employee dashboard says **Managed by <organization>**.
7. Run `python health/fleet_health.py --gateway https://owg.example.com` from an administrator workstation.
8. Leave a pilot running through sleep/restart for at least one working week before broad rollout.

## Why personal/single-use enrollment

The managed file has to be readable by the OpenWorkGraph process running as the employee. A reusable organization-wide enrollment secret therefore does not belong in that file. Use personal or otherwise single-use enrollment codes so a consumed code is no longer useful after the endpoint joins.

## Intune

Preferred production pilot: package this deployment kit as a Win32 app with **Install behavior: System**. The script detects SYSTEM and uses the machine-wide enterprise installer automatically:

```powershell
powershell.exe -ExecutionPolicy Bypass -File install-managed.ps1 -JoinCode "<per-device-code>" -MachineWide
```

For a small no-admin pilot, run the same script in **User** context without `-MachineWide`; it uses the normal per-user installer and the per-user managed-config fallback.

Detection script:

```powershell
powershell.exe -ExecutionPolicy Bypass -File detect.ps1
```

Machine-wide runtime code is read-only under Program Files. At sign-in, the enterprise launcher mirrors only the mutable source layer into that employee's LocalAppData and keeps `data/` and `config.json` there.

## Jamf

Run `install-managed.sh` as root with the per-device code supplied through your Jamf policy mechanism:

```bash
OWG_JOIN_CODE="<per-device-code>" ./install-managed.sh
```

The normal package installs the app into `/Applications`; each logged-in user keeps local evidence in their own Application Support directory.

## Browser management

`browser/` contains Chrome and Edge force-install policy templates. They deliberately contain `REPLACE_EXTENSION_ID`: a stable managed policy cannot exist until the extension has a real store ID. The release process produces a store-ready ZIP so publication becomes an account/review step rather than a packaging task.

## Upgrade model

- MDM owns fleet upgrades: deploy the newer stable installer over the old version.
- Local evidence and config are preserved by the existing installer architecture.
- OpenWorkGraph itself only performs a privacy-safe public release check; it does not silently replace enterprise-managed binaries.

## Validation before broad rollout

Use `health/fleet_health.py` plus the checklist in `FLEET_ACCEPTANCE.md`. The product's automated CI validates packages and Gateway behavior, but physical sleep/wake, reboot/login, corporate endpoint protection, real IdP, and MDM policy application must be exercised on representative managed hardware.
