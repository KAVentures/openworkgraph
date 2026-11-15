"""Security regression: a revoked run/case binding cannot be revived by unsafe SQLite files."""
import os
import sqlite3

import pytest

from server import open_work_run_case_binding as binding
from server.open_work_case_declarations import CaseDeclarationError


@pytest.fixture
def private_auth(tmp_path, monkeypatch):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    monkeypatch.setattr(binding, "auth_dir", lambda: root)
    return root


def _ledger(root):
    return root / ".open_work_run_case_bindings.sqlite3"


def test_binding_ledger_refuses_permissive_file(private_auth):
    path = _ledger(private_auth)
    sqlite3.connect(path).close()
    if os.name != "posix":
        pytest.skip("POSIX permission bits")
    path.chmod(0o644)
    with pytest.raises(CaseDeclarationError, match="unsafe binding ledger"):
        with binding._database(False):
            pass


@pytest.mark.parametrize("suffix", ["-wal", "-shm"])
def test_binding_ledger_refuses_persistent_sidecars(private_auth, suffix):
    path = _ledger(private_auth)
    sqlite3.connect(path).close()
    path.with_name(path.name + suffix).write_bytes(b"untrusted")
    with pytest.raises(CaseDeclarationError, match="sidecar"):
        with binding._database(False):
            pass


def test_binding_ledger_refuses_unsafe_rollback_journal(private_auth):
    path = _ledger(private_auth)
    sqlite3.connect(path).close()
    journal = path.with_name(path.name + "-journal")
    journal.symlink_to(private_auth / "missing")
    with pytest.raises(CaseDeclarationError, match="rollback journal"):
        with binding._database(False):
            pass


def test_binding_ledger_accepts_private_delete_journal(private_auth):
    with binding._database(True) as db:
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
