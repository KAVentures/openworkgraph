"""Owned projected-source spool, including cleanup after abrupt process death.

This is not another event store. Only marked per-profile directories below the
configured event-log data directory are ever removed. Caller holds workspace
lifecycle lock; source/profile revision guards run before staging is prepared.
"""
from pathlib import Path
import os
import re
import shutil
import stat

from .jira_retained_source import JiraIngestionError

_NAME = 'jira_ingestion_staging'
_MARKER = '.owg-owned-jira-staging'
_FORMAT = 'openworkgraph.jira-staging.v1\n'


def _redirected(path):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    # Python 3.11 Path.is_symlink alone does not reject NTFS junctions.
    return (stat.S_ISLNK(info.st_mode)
            or bool(getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)))


def _directory(data_dir, profile_id):
    if not isinstance(profile_id, str) or not re.fullmatch(r'jira_[0-9a-f]{24}', profile_id):
        raise JiraIngestionError('invalid_jira_staging_owner')
    parent = Path(data_dir) / _NAME
    target = parent / profile_id
    if (_redirected(parent) or _redirected(target)
            or (parent.exists() and not parent.is_dir())
            or (target.exists() and not target.is_dir())):
        raise JiraIngestionError('jira_staging_path_unavailable')
    return parent, target


def _owned(target, profile_id):
    marker = target / _MARKER
    expected = (_FORMAT + profile_id + '\n').encode()
    return (not _redirected(marker) and marker.is_file()
            and marker.stat().st_size == len(expected) and marker.read_bytes() == expected)


def prepare(data_dir, profile_id):
    try:
        parent, target = _directory(data_dir, profile_id)
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if target.exists():
            if not _owned(target, profile_id):
                raise JiraIngestionError('jira_staging_owner_unverified')
        else:
            target.mkdir(mode=0o700)
            # Marker is written before any projected source bytes. A crash
            # before this leaves an empty directory, safe to remove on retry.
            with (target / _MARKER).open('xb') as marker:
                marker.write((_FORMAT + profile_id + '\n').encode())
        return target
    except OSError:
        raise JiraIngestionError('jira_staging_path_unavailable') from None


def _partial_marker_only(target, profile_id):
    marker = target / _MARKER
    # Inspect at most two entries. Never classify any payload-bearing or
    # unknown directory as an incomplete ownership-marker write.
    with os.scandir(target) as entries:
        first = next(entries, None)
        second = next(entries, None)
    if first is None or Path(first.path) != marker or second is not None or _redirected(marker) or not marker.is_file():
        return False
    expected = (_FORMAT + profile_id + '\n').encode()
    size = marker.stat().st_size
    return size < len(expected) and expected.startswith(marker.read_bytes())


def clear(data_dir, profile_id):
    try:
        _parent, target = _directory(data_dir, profile_id)
        if not target.exists():
            return
        if _owned(target, profile_id):
            shutil.rmtree(target)
        else:
            if _partial_marker_only(target, profile_id):
                (target / _MARKER).unlink()
            # Never delete unmarked contents, including a substituted marker.
            # The empty pre-marker crash window contains no source payload.
            try:
                target.rmdir()
            except OSError:
                raise JiraIngestionError('jira_staging_owner_unverified') from None
    except OSError:
        raise JiraIngestionError('jira_staging_cleanup_unavailable') from None
