"""Fixed loopback HTTP transport child for the customer-operated replay harness.

No redirects, proxy discovery, credential inheritance, shell, or arbitrary URL.
The parent enforces the wall-clock timeout and reads only this bounded result.
"""
import hashlib
import http.client
import json
import re
import sys

MAX_RESPONSE = 128 * 1024
MAX_REQUEST = 1024 * 1024
PATH = re.compile(r"^/[A-Za-z0-9_/-]{1,160}$")


def strict_json(raw):
    def pairs(items):
        value = {}
        for k, v in items:
            if k in value:
                raise ValueError("duplicate")
            value[k] = v
        return value
    def constant(_):
        raise ValueError("nonfinite")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)


def exchange(value):
    port, path, timeout = value["port"], value["path"], value["timeout_ms"]
    if (type(port) is not int or not 1024 <= port <= 65535 or
            not isinstance(path, str) or not PATH.fullmatch(path) or "//" in path or
            type(timeout) is not int or not 100 <= timeout <= 120000):
        raise ValueError("invalid loopback transport")
    request = value["request"]
    body = json.dumps(request, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    if len(body) > MAX_REQUEST:
        raise ValueError("request too large")
    result = {"status":"transport_failed","http_status":None,"response_sha256":None,
              "response_bytes":None,"cost_microunits":None,"reason":"connection_or_protocol"}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout / 1000)
    try:
        conn.request("POST", path, body=body, headers={
            "Content-Type":"application/json","Accept":"application/json","Connection":"close"})
        response = conn.getresponse()
        result["http_status"] = response.status
        if response.status != 200:
            result["reason"] = "non_200_status"
            return result
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            result.update(status="invalid_output", reason="response_too_large")
            return result
        result.update(response_sha256=hashlib.sha256(raw).hexdigest(), response_bytes=len(raw))
        try:
            data = strict_json(raw)
            if (not isinstance(data, dict) or set(data) != {"format","slot_id","plan","cost_microunits"} or
                    data["format"] != "openworkgraph.procurement-http-response.v1" or
                    data["slot_id"] != request["slot_id"] or
                    (data["cost_microunits"] is not None and
                     (type(data["cost_microunits"]) is not int or not 0 <= data["cost_microunits"] <= 10**12))):
                raise ValueError("invalid envelope")
            # A malformed plan within a valid envelope is deliberately retained
            # for the existing offline evaluator, not scored by the endpoint.
            if len(json.dumps(data["plan"], allow_nan=False, separators=(",", ":")).encode()) > MAX_RESPONSE:
                raise ValueError("plan too large")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            result.update(status="invalid_output", reason="invalid_response_envelope")
            return result
        result.update(status="completed", reason="response_received", plan=data["plan"],
                      cost_microunits=data["cost_microunits"])
        return result
    except TimeoutError:
        result.update(status="timeout", reason="socket_timeout")
        return result
    except (OSError, http.client.HTTPException, ValueError):
        return result
    finally:
        conn.close()


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST + 65537)
        if len(raw) > MAX_REQUEST + 65536:
            raise ValueError("input too large")
        result = exchange(strict_json(raw))
        sys.stdout.write(json.dumps(result, separators=(",", ":"), allow_nan=False))
    except (ValueError, KeyError, TypeError, RecursionError, UnicodeError, OverflowError):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
