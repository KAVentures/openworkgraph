"""Live Graph mailbox identity preflight: transport, consent and scope regressions."""
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from server.m365_outlook_mailbox_identity import (
    _parse_identity, preflight, IdentityError, URL,
)


class Response:
    status = 200
    headers = {"Content-Type": "application/json"}
    def __init__(self, url, body):
        self.url, self.body = url, body
    def geturl(self):
        return self.url
    def read(self, n):
        return self.body[:n]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class Transport:
    def __init__(self, body, redirect=False):
        self.body, self.redirect = body, redirect
        self.requests = []
    def open(self, req, timeout):
        self.requests.append(req)
        return Response("https://elsewhere.invalid" if self.redirect else req.full_url, self.body)


class IdentityTests(unittest.TestCase):
    def test_parser_exact_casefold_identity(self):
        raw = json.dumps({"id": "opaque-id", "userPrincipalName": "A@Example.com",
                          "mail": None}).encode()
        self.assertEqual(_parse_identity(raw, "a@example.com"), ("opaque-id", True, False))
        with self.assertRaises(IdentityError):
            _parse_identity(raw, "other@example.com")
        with self.assertRaises(IdentityError):
            _parse_identity(raw.replace(b'"id":', b'"id": "x", "id":'), "a@example.com")
        with self.assertRaises(IdentityError):
            _parse_identity(json.dumps({"id": "opaque", "userPrincipalName": 42,
                "mail": "a@example.com"}).encode(), "a@example.com")
        self.assertEqual(_parse_identity(json.dumps({"id": "opaque",
            "userPrincipalName": "other@example.com", "mail": "A@example.com"}).encode(),
            "a@example.com"), ("opaque", False, True))

    def test_consent_refusal_precedes_network(self):
        with TemporaryDirectory() as tmp:
            t = Transport(b"")
            with self.assertRaises(IdentityError):
                preflight(tenant="tenant", mailbox="mail", folder="folder",
                    expected_address="a@example.com", grant_file=Path(tmp) / "grant.json",
                    output=Path(tmp) / "mailbox-identity-test.json", token="token", opener=t)
            self.assertEqual(t.requests, [])

    def test_live_get_private_report_and_revocation(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            parent.chmod(0o700)
            grant = parent / "grant.json"
            grant.write_text(json.dumps({"version": 1, "enabled": True, "consented": True,
                "tenant_scope": "tenant", "mailbox_scope": "mail", "folder_id": "folder"}))
            grant.chmod(0o600)
            output = parent / "mailbox-identity-test.json"
            body = json.dumps({"id": "opaque", "userPrincipalName": "a@example.com",
                "mail": None}).encode()
            t = Transport(body)
            kwargs = dict(tenant="tenant", mailbox="mail", folder="folder",
                expected_address="a@example.com", grant_file=grant, output=output,
                token="token", consent_graph_read=True, consent_private_report=True,
                opener=t)
            result = preflight(**kwargs)
            self.assertTrue(result["mailbox_identifier_matched"])
            self.assertEqual(t.requests[0].full_url, URL)
            self.assertEqual(t.requests[0].get_method(), "GET")
            report = json.loads(output.read_text())
            self.assertFalse(report["tenant_id_verified"])
            self.assertFalse(report["checkpoint_advanced"])
            self.assertNotIn("a@example.com", output.read_text())
            with self.assertRaises(IdentityError):
                preflight(**kwargs)
            grant.write_text(json.dumps({"version": 1, "enabled": False, "consented": True,
                "tenant_scope": "tenant", "mailbox_scope": "mail", "folder_id": "folder"}))
            with self.assertRaises(ValueError):
                preflight(**dict(kwargs, output=parent / "mailbox-identity-second.json"))

    def test_redirect_and_mismatch_refused_without_report(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            parent.chmod(0o700)
            grant = parent / "grant.json"
            grant.write_text(json.dumps({"version": 1, "enabled": True, "consented": True,
                "tenant_scope": "tenant", "mailbox_scope": "mail", "folder_id": "folder"}))
            grant.chmod(0o600)
            output = parent / "mailbox-identity-test.json"
            body = json.dumps({"id": "opaque", "userPrincipalName": "other@example.com",
                "mail": None}).encode()
            kwargs = dict(tenant="tenant", mailbox="mail", folder="folder",
                expected_address="a@example.com", grant_file=grant, output=output,
                token="token", consent_graph_read=True, consent_private_report=True)
            for t in (Transport(body), Transport(body, redirect=True)):
                with self.assertRaises(IdentityError):
                    preflight(**kwargs, opener=t)
                self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
