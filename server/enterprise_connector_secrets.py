"""Deployment-owned, local secret-file delivery for enterprise connectors.

References are existing logical environment variable names. An explicitly
mapped file is authoritative; never fall back to an old environment secret.
No network retrieval, secret material persistence or dashboard secret input.
"""
import json
import os
from pathlib import Path
import re
import stat

CATALOG="OWG_CONNECTOR_SECRET_FILES"
ROOT="OWG_CONNECTOR_SECRET_ROOT"
REF=re.compile(r"(?:OWG_(?:REST_TOKEN_|SLACK_TOKEN_|SAP_P2P_|DATAVERSE_SECRET_|HUBSPOT_TOKEN_|DATABRICKS_TOKEN_|SNOWFLAKE_TOKEN_|NETSUITE_KEY_|SQL_DSN_)[A-Z0-9_]{1,48}|OWG_CONNECTOR_WORKER_TOKEN)\Z")
NAME=re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
MAX_BYTES=16384


class SecretError(ValueError):
    pass


def _pairs(items):
    result={}
    for k,v in items:
        if k in result:
            raise ValueError()
        result[k]=v
    return result


def catalog():
    raw=os.environ.get(CATALOG,"{}")
    try:
        if len(raw)>32768:
            raise ValueError()
        value=json.loads(raw,object_pairs_hook=_pairs)
        if not isinstance(value,dict) or len(value)>100:
            raise ValueError()
        if any(not isinstance(k,str) or not REF.fullmatch(k) or
               not isinstance(v,str) or not NAME.fullmatch(v) or v in (".","..") for k,v in value.items()):
            raise ValueError()
        if len(set(value.values()))!=len(value):
            raise ValueError()
        return value
    except (ValueError,TypeError,RecursionError):
        raise SecretError("invalid_connector_secret_catalog") from None


def _file(name):
    if os.name!="posix" or not hasattr(os,"O_NOFOLLOW") or not hasattr(os,"O_DIRECTORY"):
        raise SecretError("connector_secret_files_require_posix")
    raw=os.environ.get(ROOT,"")
    path=Path(raw)
    if not raw or not path.is_absolute() or path==Path("/") or ".." in path.parts:
        raise SecretError("invalid_connector_secret_root")
    # Open every directory component without following symlinks. Ancestors
    # must be owned by root or the current user, and cannot be writable by
    # other users except sticky directories such as /tmp.
    fd=None
    try:
        fd=os.open("/",os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        for part in path.parts[1:]:
            nextfd=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nextfd
            info=os.fstat(fd)
            if info.st_uid not in (0,os.getuid()) or info.st_mode&0o022 and not info.st_mode&stat.S_ISVTX:
                raise SecretError("unsafe_connector_secret_ancestor")
        root_info=os.fstat(fd)
        if root_info.st_uid!=os.getuid() or root_info.st_mode&0o077:
            raise SecretError("connector_secret_root_must_be_owner_only")
        filefd=os.open(name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        with os.fdopen(filefd,"rb") as stream:
            before=os.fstat(stream.fileno())
            if (not stat.S_ISREG(before.st_mode) or before.st_uid!=os.getuid()
                or before.st_mode&0o077 or before.st_nlink!=1 or not 1<=before.st_size<=MAX_BYTES):
                raise SecretError("unsafe_connector_secret_file")
            raw=stream.read(MAX_BYTES+1)
            after=os.fstat(stream.fileno())
            if (len(raw)!=before.st_size or len(raw)>MAX_BYTES or
                (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=
                (after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns,after.st_ctime_ns)):
                raise SecretError("connector_secret_changed_during_read")
        # A secret manager may append exactly one newline. Do not silently
        # trim arbitrary whitespace or accept multiple embedded lines.
        if raw.endswith(b"\n"):
            raw=raw[:-1]
            if raw.endswith(b"\r"):
                raw=raw[:-1]
        value=raw.decode("utf-8")
        if not value or any(ord(c)<32 or ord(c)==127 for c in value):
            raise SecretError("invalid_connector_secret_file_content")
        return value
    except (OSError,UnicodeError):
        raise SecretError("connector_secret_file_unavailable") from None
    finally:
        if fd is not None:
            os.close(fd)


def resolve(reference):
    # Existing providers retain their own stricter reference grammar and
    # token/DSN validation. Never read arbitrary files or arbitrary env vars.
    if not isinstance(reference,str) or not REF.fullmatch(reference):
        raise SecretError("invalid_connector_secret_reference")
    entries=catalog()
    if reference in entries:
        return _file(entries[reference])
    return os.environ.get(reference,"")


def delivery(reference):
    if not isinstance(reference,str) or not REF.fullmatch(reference):
        raise SecretError("invalid_connector_secret_reference")
    return "private_file" if reference in catalog() else "environment"
