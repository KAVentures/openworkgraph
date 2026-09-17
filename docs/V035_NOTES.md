# v0.35 implementation notes

- OWNER is a presentation-only identity for the local user.
- Gmail/Outlook composite checkbox labels preserve workflow subjects while masking detected people.
- Previously learned high-confidence people can be recognized later using a keyed-hash local alias registry; literal aliases are not persisted there.
- Capture, raw storage, normalization, context construction, workflow inference, event IDs, counts, timing and task boundaries remain unchanged.
- The macOS tester ZIP returns to a single self-contained launcher and is packaged with `ditto`.
