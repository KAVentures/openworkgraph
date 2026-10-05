# OpenWorkGraph Browser Sensor v1.15

Local structural browser capture for OpenWorkGraph.

The sensor records navigation, tab activation, pointer activation, focus on editable controls, form submissions, control changes, and copy/paste occurrence. It does **not** record typed values, clipboard contents, password contents, URL query strings, or fragments.

## Pairing

Normal pairing is now explicit but code-free:

1. Keep OpenWorkGraph running.
2. Open the extension and choose **Connect to OpenWorkGraph**.
3. The extension opens a localhost approval page.
4. Choose **Connect browser**.

The resulting credential is bound to the local OpenWorkGraph installation and stored only in browser extension storage. The older 8-digit dashboard pairing code remains a fallback.

Unpacked development builds can also receive a generated local `pairing.json` from the desktop package.

## Enterprise distribution

GitHub Releases build a store-ready ZIP of this directory. Publish that ZIP through the organization-owned Chrome Web Store / Edge Add-ons account, then use the policy templates in `enterprise/browser/` to force-install the real extension ID. Store publication itself requires external publisher credentials and review and is not faked by the open-source build.
