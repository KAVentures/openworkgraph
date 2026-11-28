import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.verify_pr_preservation import FORMAT, audit


def git(repo, *args):
    env = {k: v for k, v in os.environ.items() if not k.startswith('GIT_')}
    env.update(GIT_AUTHOR_NAME='Test', GIT_AUTHOR_EMAIL='test@example.invalid',
               GIT_COMMITTER_NAME='Test', GIT_COMMITTER_EMAIL='test@example.invalid')
    return subprocess.check_output(['git', '-C', str(repo), *args], env=env).decode().strip()


def commit(repo, message):
    git(repo, 'add', '-A')
    git(repo, 'commit', '-qm', message)
    return git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def history(tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    git(repo, 'init', '-q', '-b', 'main')
    for name in ('deleted', 'restored', 'reverted', 'edited'):
        (repo / name).write_text('base')
    base = commit(repo, 'base')
    git(repo, 'checkout', '-qb', 'source')
    for name in ('deleted', 'restored'):
        (repo / name).unlink()
    for name in ('reverted', 'edited', 'missing', 'exact'):
        (repo / name).write_text('source')
    head = commit(repo, 'original feature')
    git(repo, 'checkout', '-q', 'main')
    git(repo, 'merge', '--no-ff', '-qm', 'integrate original', head)
    (repo / 'restored').write_text('new content')
    (repo / 'reverted').write_text('base')
    (repo / 'edited').write_text('changed')
    (repo / 'missing').unlink()
    candidate = commit(repo, 'integration edits')
    inventory = {'format': FORMAT, 'candidate': candidate, 'sources': [
        {'pr': 41, 'base': base, 'head': head}]}
    return repo, inventory


def test_ancestry_does_not_prove_preservation(history):
    repo, inventory = history
    before = git(repo, 'show-ref')
    result = audit(repo, inventory)
    report = result['predecessors'][0]
    assert report['source_head_is_ancestor']
    assert {r['path']: r['status'] for r in report['paths']} == {
        'deleted': 'deletion_preserved', 'restored': 'deletion_reintroduced',
        'reverted': 'reverted_to_base', 'edited': 'changed_requires_review',
        'missing': 'missing', 'exact': 'exact'}
    assert report['unresolved_paths'] == 4
    assert not result['functional_equivalence_established']
    assert not result['retirement_authorized'] and not result['merge_authorized']
    assert git(repo, 'show-ref') == before and not git(repo, 'status', '--porcelain')


def test_exact_tree_without_source_ancestry(history):
    repo, inventory = history
    source = inventory['sources'][0]
    tree = git(repo, 'rev-parse', source['head']+'^{tree}')
    inventory['candidate'] = git(repo, 'commit-tree', tree, '-p', source['base'], '-m', 'squash')
    report = audit(repo, inventory)['predecessors'][0]
    assert not report['source_head_is_ancestor']
    assert report['unresolved_paths'] == 0
    assert report['review_priority'] == 'ancestry_not_preserved'


def test_mode_only_change_and_rename(history):
    repo, inventory = history
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'mv', 'exact', 'renamed')
    git(repo, 'add', '-A')
    git(repo, 'update-index', '--chmod=+x', 'edited')
    git(repo, 'commit', '-qm', 'rename and mode')
    head = git(repo, 'rev-parse', 'HEAD')
    inventory.update(candidate=head, sources=[{'pr': 42, 'base': base, 'head': head}])
    result = audit(repo, inventory)['predecessors'][0]
    assert {r['path']:r['status'] for r in result['paths']} == {
        'exact':'deletion_preserved', 'renamed':'exact', 'edited':'exact'}
    edited = next(r for r in result['paths'] if r['path']=='edited')
    assert edited['base']['oid'] == edited['source']['oid']
    assert edited['source']['mode'] == '100755'


@pytest.mark.parametrize('bad', ['HEAD', '--help', 'deadbeef', '0'*40])
def test_refuses_unpinned_or_missing_objects(history, bad):
    repo, inventory = history
    inventory['candidate'] = bad
    with pytest.raises(ValueError):
        audit(repo, inventory)


def test_rejects_duplicate_and_reversed_base(history):
    repo, inventory = history
    source = inventory['sources'][0]
    inventory['sources'].append(source.copy())
    with pytest.raises(ValueError): audit(repo, inventory)
    inventory['sources'] = [{**source, 'base':source['head'], 'head':source['base']}]
    with pytest.raises(ValueError): audit(repo, inventory)


def test_rejects_shallow(history, tmp_path):
    repo, inventory = history
    clone = tmp_path/'shallow'
    subprocess.check_call(['git','clone','-q','--depth','1',repo.as_uri(),str(clone)])
    with pytest.raises(ValueError, match='non-shallow'): audit(clone, inventory)


def test_cli(history, tmp_path):
    repo, inventory = history
    path = tmp_path/'inventory.json'
    path.write_text(json.dumps(inventory), encoding='utf-8')
    script = Path(__file__).resolve().parents[1]/'scripts'/'verify_pr_preservation.py'
    result = subprocess.run([sys.executable,str(script),str(repo),str(path)],
                            capture_output=True, text=True, check=True)
    assert json.loads(result.stdout) == audit(repo, inventory)
    path.write_text('{"format":1,"format":2}', encoding='utf-8')
    result = subprocess.run([sys.executable,str(script),str(repo),str(path)],capture_output=True)
    assert result.returncode == 2 and not result.stdout


def test_replace_refs_do_not_change_audit(history):
    repo, inventory = history
    expected = audit(repo, inventory)
    source = inventory['sources'][0]
    git(repo, 'replace', source['head'], source['base'])
    assert audit(repo, inventory) == expected


def test_promisor_configuration_refused(history):
    repo, inventory = history
    git(repo, 'config', 'remote.origin.promisor', 'true')
    with pytest.raises(ValueError, match='promisor'): audit(repo, inventory)


def test_gitlink_changes_not_hidden_by_config(history):
    repo, inventory = history
    source = inventory['sources'][0]
    base = inventory['candidate']
    git(repo, 'config', 'diff.ignoreSubmodules', 'all')
    git(repo, 'update-index', '--add', '--cacheinfo', '160000,'+source['head']+',sub')
    git(repo, 'commit', '-qm', 'gitlink')
    head = git(repo, 'rev-parse', 'HEAD')
    inventory.update(candidate=head, sources=[{'pr': 99, 'base':base, 'head':head}])
    rows = audit(repo, inventory)['predecessors'][0]['paths']
    assert len(rows) == 1 and rows[0]['source']['kind'] == 'commit'
    assert rows[0]['status'] == 'exact'
