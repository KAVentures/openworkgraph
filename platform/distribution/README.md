# Distribution helpers

Source-side launcher/helper assets used to build OpenWorkGraph release packages live here. The generated macOS/Windows artifacts deliberately keep their established user-facing filenames and installed payload compatibility paths.

- [launchers/](launchers/) contains source-side startup/demo/browser helper launchers.
- [installers/](installers/) contains release-matched bootstrap and uninstall helpers.

The repository source tree is canonical below these folders; release builders materialize compatibility copies at historical payload-root filenames.
