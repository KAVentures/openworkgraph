# Agent reference generation and privacy redaction

Issue #490 exposed a rare collision between generated opaque references and the
ordinary sensitive-number detector. For example,
`ar:fb6b48c4579456233864e368` contains the Luhn-valid numeric substring
`4579456233864`. Redacting that substring during persistence changed the run
identity and broke structural evidence joins.

`server/agent_session_store.py::_opaque` now checks each generated reference
against the existing sensitive-identifier redactor. The original HMAC, namespace,
length and encoding remain unchanged whenever the candidate survives redaction.
For a collision, the helper deterministically tries a domain-separated HMAC
over the same native identifier and installation key. At most 64 candidates are
evaluated; exhausting the bound raises an error instead of emitting an unstable
reference or looping. Hook/native-session references use the same helper, so
the same native session or tool still has the same reference across sensors.
No native identifier is persisted by this operation.

There is **no new redaction exemption**. Arbitrary incoming `as:`, `ar:` or `at:`
strings, adjacent content and other metadata still undergo the existing privacy
rules. A syntactically valid opaque-looking string is not proof of local origin.
Existing idempotent normalization of already-prefixed adapter values is retained;
it does not bypass persistence redaction. No trusted-marker metadata, secret
registry, schema migration or permission is added.

All noncolliding historical references remain byte-identical. The already
corrupted subset cannot be reconstructed from a redacted token alone. An
affected native identifier now derives a different stable reference; old rows
are not rewritten or silently joined by time, and source evidence is needed to
establish any historical association. Previously spooled prefixed references are
not repaired or exempted. Installation-key replacement already changes these
references; this change adds no key migration or cross-install identity claim.

Tests force the exact reported digest for sessions, turns, tools, files and
messages, compare the alternate against an independent HMAC calculation, verify
unchanged historical bytes, and bound permanent failure. Actual direct ingestion
and a JSON-serialized leased spool round trip check raw, normalized and context
tables plus native-tool identity agreement. Forged prefixed card numbers and
adjacent secrets remain redacted. Capture consent, pause/stop, retention, source
access and AI permissions are unchanged.

Native Windows verification exposed a separate cold-start edge in this actual
spool journey. When a fresh directory's synthesized default and the supplied
run timestamp coincide on a coarse clock, initialization previously returned
before writing `capture_control.json`. The spool correctly refused that missing
recording decision. Initialization now treats an equal timestamp as a no-op
only when the state file already exists. Same-run paused/stopped states and
their skip intervals remain byte-identical. Deterministic equal-clock tests
cover explicit/implicit start times and both off states; the actual spool test
now forces that clock collision without bypassing lease or Observe checks.
