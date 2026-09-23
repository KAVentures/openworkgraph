# Signed release path

OpenWorkGraph keeps the existing tester ZIP workflow unchanged. A separate, manually triggered `signed-installers` workflow creates signed installer artifacts when the repository has valid platform signing credentials.

This separation is intentional: missing or expired certificates must never break normal pull-request validation or the existing local-first tester packages.

## macOS

The signed workflow:

1. builds the existing macOS payload;
2. imports a **Developer ID Installer** certificate into a temporary CI keychain;
3. creates a `.pkg` installer targeting `/Applications/OpenWorkGraph`;
4. signs the installer with `productsign`;
5. submits it to Apple's notarization service using `notarytool`;
6. staples and validates the notarization ticket;
7. verifies the installer assessment with `spctl`;
8. uploads the signed `.pkg` and SHA-256 checksum as workflow artifacts.

Required GitHub Actions secrets:

- `APPLE_DEVELOPER_ID_INSTALLER_P12_BASE64`
- `APPLE_CERTIFICATE_PASSWORD`
- `APPLE_INSTALLER_IDENTITY`
- `APPLE_ID`
- `APPLE_APP_PASSWORD`
- `APPLE_TEAM_ID`

The temporary certificate/keychain is removed after the job.

### Important macOS limitation

The current desktop prototype still launches a private Python runtime after installation. A signed/notarized installer materially improves distribution trust, but it is **not the final stable macOS application identity for Accessibility/Input Monitoring (TCC)**. For a fully managed enterprise Mac deployment, the next packaging milestone should be a real signed `.app` bundle with a stable bundle identifier and embedded/signed runtime. The existing launcher is preserved until that app bundle is independently validated.

Do not market the current `.pkg` as solving every TCC/MDM approval concern.

## Windows

The signed workflow:

1. builds the existing Windows payload;
2. installs Inno Setup in CI;
3. creates a per-user OpenWorkGraph installer `.exe`;
4. signs the installer with Authenticode using `signtool.exe` and an RFC3161 timestamp;
5. verifies the Authenticode signature;
6. uploads the signed installer and SHA-256 checksum.

Required GitHub Actions secrets:

- `WINDOWS_SIGNING_PFX_BASE64`
- `WINDOWS_SIGNING_PFX_PASSWORD`

The PFX is written only to the runner's temporary directory and deleted after signing.

Organizations using Microsoft Artifact Signing or another managed signing service can replace the PFX signing step without changing the installer build.

## Running the workflow

Open **Actions → signed-installers → Run workflow** after all required secrets have been configured. The workflow is manual (`workflow_dispatch`) and is not part of ordinary PR CI.

Before publishing a signed artifact, inspect the verification output and test installation on clean managed/unmanaged machines. Code signing establishes publisher integrity; it does not replace application security review, MDM policy configuration, endpoint protection allowlisting, or privacy assessment.
