from __future__ import annotations

"""Opt-in, device-local Stripe restricted credentials for signed desktop installs.

No passwords in command-line flags, process environment, OWG events, MCP replies,
GitHub, or plaintext configuration. macOS uses the user's login Keychain via
Security.framework; Windows uses the user's Credential Manager. Unavailable,
locked, and unsupported stores fail closed. This is NOT organization-wide
OAuth approval or a multi-tenant Gateway integration.
"""

import ctypes
import ctypes.util
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import stat
import tempfile
import threading
from typing import Any, Callable

from .local_auth import auth_dir as local_auth_dir

_ACCOUNT = re.compile(r"^acct_[A-Za-z0-9]{8,90}$")
_RESTRICTED = re.compile(r"^rk_(?:test|live)_[^\s]{8,240}$")
_SERVICE = "com.kinvectum.openworkgraph.stripe-restricted-read.v1"
_METADATA_NAME = ".stripe_source_connection.json"
_LOCK = threading.RLock()


class SourceCredentialError(RuntimeError):
    """Safe user-facing error with no credential or provider response attached."""


def _serialized(fn):
    """Keep concurrent dashboard connect/disconnect/read operations ordered."""
    @wraps(fn)
    def invoke(*args, **kwargs):
        with _LOCK:
            return fn(*args, **kwargs)
    return invoke


class _MacKeychain:
    def __init__(self) -> None:
        path = ctypes.util.find_library("Security")
        if not path:
            raise SourceCredentialError("macOS Keychain is unavailable")
        cf_path = ctypes.util.find_library("CoreFoundation")
        if not cf_path:
            raise SourceCredentialError("macOS Keychain is unavailable")
        try:
            self.security = ctypes.CDLL(path)
            self.cf = ctypes.CDLL(cf_path)
        except OSError:
            raise SourceCredentialError("macOS Keychain is unavailable") from None
        self.find = self.security.SecKeychainFindGenericPassword
        self.find.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_uint32),
            ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        )
        self.find.restype = ctypes.c_int32
        self.add = self.security.SecKeychainAddGenericPassword
        self.add.argtypes = (
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
            ctypes.c_uint32, ctypes.c_char_p, ctypes.c_uint32,
            ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
        )
        self.add.restype = ctypes.c_int32
        self.modify = self.security.SecKeychainItemModifyAttributesAndData
        self.modify.argtypes = (
            ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
        )
        self.modify.restype = ctypes.c_int32
        self.delete = self.security.SecKeychainItemDelete
        self.delete.argtypes = (ctypes.c_void_p,)
        self.delete.restype = ctypes.c_int32
        self.free = self.security.SecKeychainItemFreeContent
        self.free.argtypes = (ctypes.c_void_p, ctypes.c_void_p)
        self.free.restype = ctypes.c_int32
        self.release = self.cf.CFRelease
        self.release.argtypes = (ctypes.c_void_p,)
        self.release.restype = None

    def _lookup(self, account: str, *, with_password: bool) -> tuple[int, Any, Any, int]:
        service = _SERVICE.encode("utf-8")
        name = account.encode("ascii")
        item = ctypes.c_void_p()
        length = ctypes.c_uint32()
        data = ctypes.c_void_p()
        status = self.find(
            None, len(service), service, len(name), name,
            ctypes.byref(length) if with_password else None,
            ctypes.byref(data) if with_password else None,
            ctypes.byref(item),
        )
        return int(status), item, data, int(length.value)

    def read(self, account: str) -> str | None:
        status, item, data, length = self._lookup(account, with_password=True)
        try:
            if status == -25300:  # errSecItemNotFound
                return None
            if status != 0:
                raise SourceCredentialError("macOS Keychain credential is unavailable")
            if not data or length > 512:
                raise SourceCredentialError("macOS Keychain returned invalid credential data")
            return ctypes.string_at(data, length).decode("utf-8")
        finally:
            if data:
                self.free(None, data)
            if item:
                self.release(item)

    def write(self, account: str, secret: str) -> None:
        status, item, _data, _length = self._lookup(account, with_password=False)
        password = secret.encode("utf-8")
        buffer = ctypes.create_string_buffer(password)
        try:
            if status == -25300:
                service = _SERVICE.encode("utf-8")
                name = account.encode("ascii")
                code = self.add(
                    None, len(service), service, len(name), name,
                    len(password), ctypes.cast(buffer, ctypes.c_void_p), None,
                )
            elif status == 0 and item:
                code = self.modify(item, None, len(password), ctypes.cast(buffer, ctypes.c_void_p))
            else:
                raise SourceCredentialError("macOS Keychain credential cannot be updated")
            if code != 0:
                raise SourceCredentialError("macOS Keychain refused the credential update")
        finally:
            if item:
                self.release(item)

    def remove(self, account: str) -> None:
        status, item, _data, _length = self._lookup(account, with_password=False)
        try:
            if status == -25300:
                return
            if status != 0 or not item or self.delete(item) != 0:
                raise SourceCredentialError("macOS Keychain refused credential removal")
        finally:
            if item:
                self.release(item)


class _WindowsCredentialManager:
    def __init__(self) -> None:
        try:
            import win32cred  # type: ignore
        except ImportError as exc:
            raise SourceCredentialError("Windows Credential Manager is unavailable") from exc
        self.api = win32cred

    def _target(self, account: str) -> str:
        return _SERVICE + ":" + account

    def read(self, account: str) -> str | None:
        try:
            record = self.api.CredRead(self._target(account), self.api.CRED_TYPE_GENERIC, 0)
        except Exception as exc:
            if getattr(exc, "winerror", None) == 1168:
                return None
            raise SourceCredentialError("Windows Credential Manager is unavailable") from None
        value = record.get("CredentialBlob")
        try:
            if isinstance(value, str):
                return value
            if isinstance(value, bytes):
                return value.decode("utf-16-le")
        except UnicodeError:
            pass
        raise SourceCredentialError("Windows Credential Manager returned invalid data")

    def write(self, account: str, secret: str) -> None:
        try:
            self.api.CredWrite({
                "Type": self.api.CRED_TYPE_GENERIC,
                "TargetName": self._target(account),
                "CredentialBlob": secret.encode("utf-16-le"),
                "Persist": self.api.CRED_PERSIST_LOCAL_MACHINE,
                "UserName": account,
            }, 0)
        except Exception:
            raise SourceCredentialError("Windows Credential Manager refused the credential update") from None

    def remove(self, account: str) -> None:
        try:
            self.api.CredDelete(self._target(account), self.api.CRED_TYPE_GENERIC, 0)
        except Exception as exc:
            if getattr(exc, "winerror", None) != 1168:
                raise SourceCredentialError("Windows Credential Manager refused credential removal") from None


def _vault() -> _MacKeychain | _WindowsCredentialManager:
    system = platform.system()
    try:
        if system == "Darwin":
            return _MacKeychain()
        if system == "Windows":
            return _WindowsCredentialManager()
    except (OSError, AttributeError, TypeError):
        raise SourceCredentialError("Native OS credential store is unavailable") from None
    raise SourceCredentialError("Native credential storage is not supported on this operating system")


def _metadata_path(directory: Path | None = None) -> Path:
    return (directory or local_auth_dir()) / _METADATA_NAME


def _vault_account(account: str, directory: Path | None = None) -> str:
    """Scope OS vault access to this installation, not every OWG install/user."""
    root = _metadata_path(directory).parent.resolve()
    identifier = hashlib.sha256(os.path.normcase(str(root)).encode("utf-8")).hexdigest()[:24]
    return identifier + ":" + account


def _read_metadata(path: Path) -> dict[str, Any] | None:
    try:
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or path.is_symlink():
            return None
        if os.name == "posix" and info.st_mode & 0o077:
            return None
        if info.st_size > 1024:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError):
        return None
    if not isinstance(value, dict):
        return None
    account = value.get("account")
    if value.get("version") != 1 or value.get("enabled") is not True or not isinstance(account, str) or not _ACCOUNT.fullmatch(account):
        return None
    return {"account": account, "enabled": True, "version": 1}


def _write_metadata(path: Path, account: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise SourceCredentialError("Unsafe Stripe connection metadata path")
    fd, temp = tempfile.mkstemp(dir=path.parent, prefix=".stripe-conn-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": 1, "enabled": True, "account": account}, handle)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        if os.name == "posix":
            os.chmod(temp, 0o600)
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


@_serialized
def stripe_source_status(*, directory: Path | None = None) -> dict[str, bool | str]:
    """Never return secrets or unredacted Stripe API response data."""
    record = _read_metadata(_metadata_path(directory))
    return {
        "configured": record is not None,
        "provider": "stripe",
        "credential_storage": "native_user_keychain",
        "locally_managed": True,
        "organizational_oauth_approval": False,
    }


@_serialized
def configure_stripe_source(
    account: str, restricted_key: str, *,
    directory: Path | None = None,
    reader: Callable[[str, str], dict[str, Any]] | None = None,
) -> dict[str, bool | str]:
    """Verify exact authorized Stripe account before installing a read-only key."""
    if not _ACCOUNT.fullmatch(str(account or "")):
        raise SourceCredentialError("Invalid Stripe account identifier")
    if not _RESTRICTED.fullmatch(str(restricted_key or "")):
        raise SourceCredentialError("A restricted Stripe read-only key is required")
    # The only provider call during setup is a read of the authenticated key's
    # own Stripe account. Never test an arbitrary invoice or refund identifier.
    if reader is None:
        from .stripe_source_evidence import _get
        reader = _get
    try:
        response = reader("https://api.stripe.com/v1/account", restricted_key)
    except Exception:
        raise SourceCredentialError("Stripe account check failed; no credential saved") from None
    if not isinstance(response, dict) or response.get("object") != "account" or response.get("id") != account:
        raise SourceCredentialError("Stripe restricted key belongs to a different account")
    store = _vault()
    path = _metadata_path(directory)
    current = _read_metadata(path)
    # Store only the credential in the OS vault. Metadata contains only the
    # validated account ID and explicit enabled flag; no key or provider JSON.
    store.write(_vault_account(account, directory), restricted_key)
    try:
        _write_metadata(path, account)
    except Exception:
        if current is None or current["account"] != account:
            try:
                store.remove(_vault_account(account, directory))
            except SourceCredentialError:
                pass
        raise SourceCredentialError("Unable to save Stripe source connection") from None
    if current and current["account"] != account:
        # A rotated account must not leave a second accessible OWG credential.
        try:
            store.remove(_vault_account(current["account"], directory))
        except SourceCredentialError:
            # The old secret is orphaned, but no longer reachable through OWG.
            pass
    return stripe_source_status(directory=directory)


@_serialized
def load_stripe_source(*, directory: Path | None = None) -> dict[str, str] | None:
    """Return effective local opt-in only when the OS vault is available.

    Never allow agents to select an account or pass a key via a tool argument.
    """
    record = _read_metadata(_metadata_path(directory))
    if record is None:
        return None
    try:
        key = _vault().read(_vault_account(record["account"], directory))
    except (SourceCredentialError, OSError):
        return None
    if not isinstance(key, str) or not _RESTRICTED.fullmatch(key):
        return None
    return {
        "OWG_STRIPE_SOURCE_READ_ENABLED": "true",
        "OWG_STRIPE_ACCOUNT_ID": record["account"],
        "OWG_STRIPE_RESTRICTED_READ_KEY": key,
    }


@_serialized
def disconnect_stripe_source(*, directory: Path | None = None) -> dict[str, bool | str]:
    path = _metadata_path(directory)
    record = _read_metadata(path)
    # Disable reads immediately, even if the OS vault is locked or unavailable.
    try:
        path.unlink(missing_ok=True)
    except OSError:
        raise SourceCredentialError("Unable to disable Stripe source connection") from None
    if record is not None:
        try:
            _vault().remove(_vault_account(record["account"], directory))
        except (SourceCredentialError, OSError):
            raise SourceCredentialError("Stripe source disabled; credential deletion requires OS vault access") from None
    return stripe_source_status(directory=directory)


__all__ = [
    "SourceCredentialError", "stripe_source_status",
    "configure_stripe_source", "load_stripe_source", "disconnect_stripe_source",
]
