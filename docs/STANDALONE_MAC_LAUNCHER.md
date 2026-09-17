# Standalone macOS tester launcher

The macOS release ZIP uses a single user-facing `START_OPENWORKGRAPH.command` with the application payload embedded inside it. The launcher extracts the payload to a temporary directory, runs the existing `START_ON_MAC.command`, and the normal installer copies the application into the user's Library.

This deliberately avoids requiring Finder/Gatekeeper to launch a second executable from a hidden sibling directory inside the downloaded ZIP. The release ZIP is built with Apple's `ditto -c -k --keepParent` so executable permissions and macOS ZIP metadata are preserved.
