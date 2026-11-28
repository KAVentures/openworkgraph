"""Deterministic downloadable archive for the reviewed native shadow skill."""
from hashlib import sha256
from io import BytesIO
import json
import re
from zipfile import ZIP_STORED, ZipFile, ZipInfo

MAX_BYTES = 256 * 1024
NAME = re.compile(r'[a-z](?:[a-z0-9-]{0,62}[a-z0-9])?\Z')
FORMAT = 'openworkgraph.ai-shadow-skill-package.v1'


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')


def digest(raw):
    return sha256(raw).hexdigest()


def require(ok, message):
    if not ok:
        raise ValueError(message)


def package(payload, *, prepare=None):
    require(isinstance(payload, dict) and set(payload) == {'sources', 'proposal', 'draft'}, 'Supply exact original sources, proposal and human-reviewed draft')
    require(isinstance(payload['sources'], list) and 1 <= len(payload['sources']) <= 8, 'Select one to eight original sources')
    if prepare is None:
        from .ai_skill_stage import prepare
    files, manifest = prepare(payload['sources'], payload['proposal'], payload['draft'])
    require(isinstance(manifest, dict) and manifest.get('format') == FORMAT and manifest.get('mode') == 'shadow_only'
            and all(manifest.get(k) is False for k in ('semantic_correctness_verified', 'deployment_verified', 'client_permissions_verified', 'execution_authorized')), 'Shadow-only manifest required')
    name = manifest.get('name')
    require(isinstance(name, str) and NAME.fullmatch(name) and '--' not in name, 'Invalid skill name')
    expected = {f'claude/{name}/SKILL.md', 'CHATGPT_INSTRUCTIONS.md'}
    require(isinstance(files, dict) and set(files) == expected and isinstance(manifest.get('files'), dict)
            and set(manifest['files']) == expected, 'Unexpected package inventory')
    require(manifest.get('package_sha256') == digest(canonical({k:v for k,v in manifest.items() if k != 'package_sha256'})), 'Package manifest pin mismatch')
    for path, raw in files.items():
        require(isinstance(raw, bytes) and 0 < len(raw) <= MAX_BYTES and manifest['files'][path] == {'sha256':digest(raw), 'bytes':len(raw)}, 'Package file mismatch')
        raw.decode('utf-8', errors='strict')
    require(sum(map(len, files.values())) + len(canonical(manifest)) <= MAX_BYTES, 'Package byte budget exceeded')
    return files, manifest


def preview(payload, *, prepare=None):
    files, manifest = package(payload, prepare=prepare)
    return {'manifest':manifest, 'rendered_files':{k:v.decode('utf-8') for k,v in sorted(files.items())},
            'installation_performed':False, 'execution_authorized':False}


def archive(payload, *, prepare=None):
    require(isinstance(payload, dict) and set(payload) == {'draft_request','expected_package_sha256','confirm_shadow_download'}
            and payload['confirm_shadow_download'] is True, 'Explicit shadow package download confirmation required')
    files, manifest = package(payload['draft_request'], prepare=prepare)
    require(payload['expected_package_sha256'] == manifest['package_sha256'], 'Package preview changed; review again')
    buf = BytesIO()
    with ZipFile(buf, 'w', compression=ZIP_STORED) as out:
        for path, raw in sorted({**files, 'manifest.json':canonical(manifest)}.items()):
            info = ZipInfo(path, date_time=(1980,1,1,0,0,0))
            info.create_system = 3
            info.external_attr = 0o100600 << 16
            info.compress_type = ZIP_STORED
            out.writestr(info, raw)
    raw = buf.getvalue()
    require(len(raw) <= MAX_BYTES + 4096, 'Archive byte budget exceeded')
    return raw, manifest
