# Signing and trust gate

The open-source repository can build both desktop installers, but it cannot create Apple Developer ID or Windows Authenticode identities. Those credentials belong to the publisher account and must be supplied as GitHub Actions secrets.

The existing `signed-installers` workflow is the production trust gate.

## Apple

Configure:

- `APPLE_DEVELOPER_ID_APPLICATION_P12_BASE64`
- `APPLE_APPLICATION_CERTIFICATE_PASSWORD`
- `APPLE_APPLICATION_IDENTITY`
- `APPLE_DEVELOPER_ID_INSTALLER_P12_BASE64`
- `APPLE_INSTALLER_CERTIFICATE_PASSWORD`
- `APPLE_INSTALLER_IDENTITY`
- `APPLE_ID`
- `APPLE_APP_PASSWORD`
- `APPLE_TEAM_ID`

The workflow signs the app, signs the package, submits it to Apple notarization, staples the result, and verifies it before replacing the stable release asset.

## Windows

Configure:

- `WINDOWS_SIGNING_PFX_BASE64`
- `WINDOWS_SIGNING_PFX_PASSWORD`

The workflow signs and verifies both the normal per-user Setup EXE and the enterprise machine-wide Setup EXE, then replaces their stable release assets.

## Release rule

Do not describe an unsigned build as frictionless enterprise software. A production pilot should use the signed assets and pass `FLEET_ACCEPTANCE.md`.
