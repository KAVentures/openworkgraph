# Signed release path

OpenWorkGraph keeps the existing tester ZIP workflow unchanged. A separate, manually triggered `signed-installers` workflow creates signed installer artifacts when the repository has valid platform signing credentials.

This separation is intentional: missing or expired certificates must never break normal pull-request validation or the existing local-first tester packages.

## macOS

The signed workflow:

1. builds a real `OpenWorkGraph.app` menu-bar bundle with stable bundle identifier `com.kinvectum.openworkgraph`;
2. embeds a private CPython runtime plus OpenWorkGraph dependencies inside the app, so first launch does not download Python;
3. imports separate **Developer ID Application** and **Developer ID Installer** certificates into a temporary CI keychain;
4. signs embedded Mach-O code and the app with hardened runtime;
5. notarizes and staples the app itself;
6. packages the app plus its login LaunchAgent into a `.pkg`;
7. signs, notarizes and staples the installer;
8. verifies both app execution and installer assessments with `spctl`;
9. uploads the signed `.pkg` and SHA-256 checksum as workflow artifacts.

Required GitHub Actions secrets:

- `APPLE_DEVELOPER_ID_APPLICATION_P12_BASE64`
- `APPLE_APPLICATION_CERTIFICATE_PASSWORD`
- `APPLE_APPLICATION_IDENTITY`
- `APPLE_DEVELOPER_ID_INSTALLER_P12_BASE64`
- `APPLE_INSTALLER_CERTIFICATE_PASSWORD`
- `APPLE_INSTALLER_IDENTITY`
- `APPLE_ID`
- `APPLE_APP_PASSWORD`
- `APPLE_TEAM_ID`

The temporary certificates/keychain are removed after the job.

The app starts as a menu-bar process and the installer includes a LaunchAgent for login start. Code signing gives the executable a stable application identity suitable for managed Accessibility/Input Monitoring approval, but organizations still need to configure the corresponding MDM/TCC policy where required.

## Windows

The signed workflow:

1. builds the existing Windows payload;
2. embeds a private CPython runtime plus OpenWorkGraph dependencies, so first launch does not download Python and does not require PowerShell;
3. installs Inno Setup in CI;
4. creates a per-user OpenWorkGraph installer `.exe` with tray launch and optional login autostart;
5. signs the installer with Authenticode using `signtool.exe` and an RFC3161 timestamp;
6. verifies the Authenticode signature;
7. uploads the signed installer and SHA-256 checksum.

Required GitHub Actions secrets:

- `WINDOWS_SIGNING_PFX_BASE64`
- `WINDOWS_SIGNING_PFX_PASSWORD`

The PFX is written only to the runner's temporary directory and deleted after signing.

Organizations using Microsoft Artifact Signing or another managed signing service can replace the PFX signing step without changing the installer build.

## Running the workflow

Open **Actions → signed-installers → Run workflow** after all required secrets have been configured. The workflow is manual (`workflow_dispatch`) and is not part of ordinary PR CI.

Before publishing a signed artifact, inspect the verification output and test installation on clean managed/unmanaged machines. Code signing establishes publisher integrity; it does not replace application security review, MDM policy configuration, endpoint protection allowlisting, or privacy assessment.
