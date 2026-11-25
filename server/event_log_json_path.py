"""Exact JSON attribute lookups for names supplied by event-log sources."""


def attribute_pointer(name: str) -> str:
    """Encode a literal object key as an RFC 6901 JSON Pointer for a bound SQL parameter."""
    return "/" + name.replace("~", "~0").replace("/", "~1")
