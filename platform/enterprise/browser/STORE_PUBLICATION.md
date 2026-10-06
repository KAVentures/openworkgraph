# Browser store publication

The release pipeline builds `OpenWorkGraph-Browser-Sensor-vX.Y.Z.zip` from the exact tested `browser_extension/` sources.

Publication still requires organization-owned Chrome Web Store / Microsoft Edge Add-ons accounts and their review processes. Those credentials cannot live in the open-source repository.

After publication:

1. Record the real extension ID.
2. Replace `REPLACE_EXTENSION_ID` in the Chrome/Edge policy template.
3. Push the browser policy through your MDM/browser management.
4. The employee clicks **Connect to OpenWorkGraph** once. Pairing opens a localhost approval page instead of requiring an 8-digit code.
5. Keep code-based pairing only as a fallback.

Do not force-install an unpacked development extension across a production fleet.
