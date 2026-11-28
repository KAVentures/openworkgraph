"""Explicit isolated Claude Code project setup for a verified OWG shadow skill."""
import argparse
import json
import os
from pathlib import Path
import re

from . import ai_skill_stage as staging

FORMAT = 'openworkgraph.native-skill-project-receipt.v1'
HEX = re.compile(r'[a-f0-9]{64}\Z')
NAME = re.compile(r'[a-z](?:[a-z0-9-]{0,62}[a-z0-9])?\Z')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def verified_skill(package, pin):
    require(isinstance(pin, str) and HEX.fullmatch(pin), 'Invalid independent package pin')
    root = Path(package)
    staging.verify(root, pin)
    manifest = staging.load(root / 'manifest.json')
    name = manifest['name']
    require(isinstance(name, str) and NAME.fullmatch(name) and '--' not in name, 'Invalid skill name')
    require(manifest.get('mode') == 'shadow_only' and all(manifest.get(k) is False for k in (
        'semantic_correctness_verified', 'deployment_verified', 'client_permissions_verified', 'execution_authorized')),
        'Expected shadow-only package')
    require(all(isinstance(manifest.get(k), str) and HEX.fullmatch(manifest[k]) for k in ('packet_sha256', 'proposal_sha256')),
            'Missing or invalid proposal lineage')
    rel = f'claude/{name}/SKILL.md'
    path = root / rel
    raw = path.read_bytes()
    require(manifest['files'][rel] == {'sha256':staging.sha(raw), 'bytes':len(raw)}, 'Package changed during review')
    require(staging.sha(staging.canonical({k:v for k,v in manifest.items() if k != 'package_sha256'})) == pin,
            'Package manifest changed during review')
    text = raw.decode('utf-8')
    lines = text.splitlines()
    require(len(lines) >= 5 and lines[0] == '---' and lines[1] == 'name: '+name and
            lines[2].startswith('description: ') and lines[3] == 'disable-model-invocation: true' and lines[4] == '---',
            'Expected the reviewed manual-invocation skill format')
    description = json.loads(lines[2][len('description: '):])
    require(isinstance(description, str) and 0 < len(description.strip()) <= 200 and '\n' not in description and '\t' not in description,
            'Invalid reviewed skill description')
    require(not re.search(r'!\s*`|`{3,}\s*!', text+'\n'+description), 'Dynamic shell injection is not permitted')
    return name, raw, manifest


def project_paths(project, package, name, *, create=False):
    root = Path(project)
    require(root.is_dir() and not root.is_symlink(), 'Select an existing real test project directory')
    root = root.resolve(strict=True)
    package = Path(package).resolve(strict=True)
    require(not root.is_relative_to(package) and not package.is_relative_to(root), 'Keep package and test project in separate directory trees')
    parent = root
    for part in ('.claude', 'skills'):
        parent = parent / part
        if create and not parent.exists() and not parent.is_symlink():
            parent.mkdir(mode=0o700, exist_ok=True)
        require(parent.is_dir() and not parent.is_symlink(), 'Missing or unsafe project skills directory')
    return parent / name


def check_installed(target, expected):
    require(target.is_dir() and not target.is_symlink(), 'Missing or unsafe installed skill directory')
    require({p.name for p in target.iterdir()} == {'SKILL.md'}, 'Unexpected installed skill contents; review manually')
    file = target / 'SKILL.md'
    require(file.is_file() and not file.is_symlink(), 'Missing or unsafe installed skill file')
    with file.open('rb') as stream:
        actual = stream.read(len(expected)+1)
    require(actual == expected, 'Installed skill changed; review manually')
    return file


def operate(mode, package, pin, project, *, confirm_isolated=False, confirm_change=False):
    require(mode in ('install', 'review', 'remove'), 'Invalid operation')
    require(confirm_isolated is True, 'Explicit isolated test project confirmation required')
    require(mode == 'review' or confirm_change is True, 'Explicit project change confirmation required')
    name, raw, manifest = verified_skill(package, pin)
    target = project_paths(project, package, name, create=mode == 'install')
    if mode == 'install':
        # Never replace an existing directory, including an identical skill.
        target.mkdir(mode=0o700, exist_ok=False)
        fd = os.open(target/'SKILL.md', os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
        check_installed(target, raw)
    elif mode == 'review':
        check_installed(target, raw)
    else:
        file = check_installed(target, raw)
        file.unlink()
        target.rmdir()  # Never recursively remove other project contents.
    body = {'format':FORMAT, 'operation':mode, 'package_sha256':pin,
            'packet_sha256':manifest['packet_sha256'], 'proposal_sha256':manifest['proposal_sha256'],
            'skill_relative_path':f'.claude/skills/{name}/SKILL.md', 'skill_sha256':staging.sha(raw),
            'exact_installed_file_checked':True, 'skill_present_after_operation':mode != 'remove',
            'project_isolation_verified':False, 'client_discovery_verified':False,
            'skill_invocation_verified':False, 'client_permissions_verified':False,
            'deployment_verified':False, 'business_outcome_verified':False, 'execution_authorized':False}
    return {**body, 'receipt_sha256':staging.sha(staging.canonical(body))}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode', choices=('install', 'review', 'remove'))
    p.add_argument('--package', required=True)
    p.add_argument('--package-sha256', required=True)
    p.add_argument('--project', required=True)
    p.add_argument('--confirm-isolated-project', action='store_true', required=True)
    p.add_argument('--confirm-change', action='store_true')
    a = p.parse_args(argv)
    try:
        result = operate(a.mode, a.package, a.package_sha256, a.project,
                         confirm_isolated=a.confirm_isolated_project, confirm_change=a.confirm_change)
        print(json.dumps(result, indent=2, ensure_ascii=False))
    except (ValueError, TypeError, KeyError, OSError, RecursionError, OverflowError):
        p.exit(2, 'Skill project operation refused; inspect the exact package and project manually. A failed write may leave a partial skill directory.\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
