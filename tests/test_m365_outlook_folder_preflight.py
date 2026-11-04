"""Folder preflight parser, transport and consent regressions."""
import io
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from server.m365_outlook_folder_preflight import _parse_folder, preflight, PreflightError


class Response:
    status = 200
    headers = {"Content-Type": "application/json; charset=utf-8"}
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
    def __init__(self, body):
        self.body = body
        self.requests = []
    def open(self, req, timeout):
        self.requests.append(req)
        return Response(req.full_url, self.body)


class PreflightTests(unittest.TestCase):
    def test_parser_exact_identity(self):
        raw = json.dumps({"id": "folder", "displayName": "Inbox",
                          "childFolderCount": 0, "totalItemCount": 4}).encode()
        self.assertEqual(_parse_folder(raw, "folder"), (0, 4))
        with self.assertRaises(PreflightError):
            _parse_folder(raw, "different")
        with self.assertRaises(PreflightError):
            _parse_folder(raw.replace(b'"id":', b'"id": "x", "id":'), "folder")

    def test_invalid_metadata(self):
        for value in ({"id": "folder", "displayName": "Inbox", "childFolderCount": True,
                       "totalItemCount": 0},
                      {"id": "folder", "displayName": "Inbox", "childFolderCount": 0,
                       "totalItemCount": -1}):
            with self.assertRaises(PreflightError):
                _parse_folder(json.dumps(value).encode(), "folder")

    def test_consent_refusal_has_no_io(self):
        with TemporaryDirectory() as tmp:
            transport = Transport(b"")
            with self.assertRaises(PreflightError):
                preflight(tenant="tenant", mailbox="mail", folder="folder",
                          grant_file=Path(tmp) / "grant.json",
                          output=Path(tmp) / "folder-preflight-test.json",
                          token="token", opener=transport)
            self.assertEqual(transport.requests, [])

    def test_real_get_and_private_report(self):
        with TemporaryDirectory() as tmp:
            parent = Path(tmp)
            parent.chmod(0o700)
            grant = parent / "grant.json"
            grant.write_text(json.dumps({"version": 1, "enabled": True, "consented": True,
                "tenant_scope": "tenant", "mailbox_scope": "mail", "folder_id": "folder"}))
            grant.chmod(0o600)
            output = parent / "folder-preflight-test.json"
            transport = Transport(json.dumps({"id": "folder", "displayName": "Inbox",
                "childFolderCount": 2, "totalItemCount": 8}).encode())
            result = preflight(tenant="tenant", mailbox="mail", folder="folder",
                grant_file=grant, output=output, token="token",
                consent_graph_read=True, consent_private_report=True, opener=transport)
            self.assertEqual(result["status"], "folder_metadata_observed")
            self.assertEqual(transport.requests[0].get_method(), "GET")
            self.assertEqual(transport.requests[0].full_url.split("?")[0],
                             "https://graph.microsoft.com/v1.0/me/mailFolders/folder")
            report = json.loads(output.read_text())
            self.assertFalse(report["provider_tenant_verified"])
            self.assertFalse(report["safe_to_retry"])
            self.assertNotIn("Inbox", output.read_text())
            with self.assertRaises(PreflightError):
                preflight(tenant="tenant", mailbox="mail", folder="folder",
                    grant_file=grant, output=output, token="token",
                    consent_graph_read=True, consent_private_report=True, opener=transport)


if __name__ == "__main__":
    unittest.main()
