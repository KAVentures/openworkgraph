import json
import pytest
from shared import ai_skill_stage as staging
from shared.ai_skill_project_install import operate, main


def fixture(tmp_path, *, instructions='Draft from synthetic inputs only.', mode='shadow_only'):
    package=tmp_path/'package'; project=tmp_path/'project'; project.mkdir()
    rel='claude/example/SKILL.md'; (package/'claude/example').mkdir(parents=True)
    skill=('---\nname: example\ndescription: "Shadow test"\ndisable-model-invocation: true\n---\n\n'+instructions+'\n').encode()
    files={rel:skill,'CHATGPT_INSTRUCTIONS.md':instructions.encode()}
    for path,raw in files.items(): (package/path).write_bytes(raw)
    manifest={'format':staging.FORMAT,'name':'example','mode':mode,'packet_sha256':'a'*64,'proposal_sha256':'b'*64,
              'files':{path:{'sha256':staging.sha(raw),'bytes':len(raw)} for path,raw in files.items()},
              **dict.fromkeys(('semantic_correctness_verified','deployment_verified','client_permissions_verified','execution_authorized'),False)}
    pin=staging.sha(staging.canonical(manifest)); (package/'manifest.json').write_bytes(staging.canonical({**manifest,'package_sha256':pin}))
    return package,pin,project,skill


def call(mode, package, pin, project, **kwargs):
    return operate(mode,package,pin,project,confirm_isolated=True,confirm_change=True,**kwargs)


def test_exact_install_review_remove_preserves_other_project_files(tmp_path):
    package,pin,project,skill=fixture(tmp_path)
    (project/'other.txt').write_text('leave intact')
    first=call('install',package,pin,project); target=project/first['skill_relative_path']
    assert target.read_bytes()==skill and first['skill_present_after_operation']
    assert first['receipt_sha256']==staging.sha(staging.canonical({k:v for k,v in first.items() if k!='receipt_sha256'}))
    assert not first['skill_invocation_verified'] and not first['client_permissions_verified']
    with pytest.raises(FileExistsError): call('install',package,pin,project)
    assert call('review',package,pin,project)['exact_installed_file_checked']
    removed=call('remove',package,pin,project)
    assert not removed['skill_present_after_operation'] and not target.exists()
    assert (project/'other.txt').read_text()=='leave intact' and (project/'.claude/skills').is_dir()


def test_changed_or_extra_content_never_removed(tmp_path):
    package,pin,project,skill=fixture(tmp_path)
    target=project/call('install',package,pin,project)['skill_relative_path']
    target.write_bytes(skill+b'changed')
    for mode in ('review','remove'):
        with pytest.raises(ValueError,match='changed'): call(mode,package,pin,project)
    assert target.read_bytes()==skill+b'changed'
    target.write_bytes(skill);extra=target.parent/'extra.txt';extra.write_text('leave')
    with pytest.raises(ValueError,match='Unexpected'): call('remove',package,pin,project)
    assert extra.exists() and target.exists()


def test_invalid_pin_confirmation_and_symlink_refusal(tmp_path):
    package,pin,project,_=fixture(tmp_path)
    for kwargs in ({'confirm_isolated':False,'confirm_change':True},{'confirm_isolated':True,'confirm_change':False}):
        with pytest.raises(ValueError,match='confirmation'): operate('install',package,pin,project,**kwargs)
    with pytest.raises(ValueError,match='pin mismatch'): call('install',package,'f'*64,project)
    assert not (project/'.claude').exists()
    outside=tmp_path/'outside';outside.mkdir();(project/'.claude').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='unsafe'): call('install',package,pin,project)
    assert list(outside.iterdir())==[]
    (project/'.claude').unlink(); target=project/call('install',package,pin,project)['skill_relative_path']
    target.unlink(); external=outside/'SKILL.md';external.write_text('untouched');target.symlink_to(external)
    with pytest.raises(ValueError,match='unsafe'): call('remove',package,pin,project)
    assert external.read_text()=='untouched'


def test_rejects_nonshadow_and_dynamic_shell_content(tmp_path):
    a=tmp_path/'a';a.mkdir();p,pin,project,_=fixture(a,mode='production')
    with pytest.raises(ValueError,match='shadow-only'): call('install',p,pin,project)
    b=tmp_path/'b';b.mkdir();p,pin,project,_=fixture(b,instructions='!`echo example`')
    with pytest.raises(ValueError,match='Dynamic shell'): call('install',p,pin,project)
    assert not (project/'.claude').exists()


def test_cli_requires_explicit_change_and_returns_receipt(tmp_path,capsys):
    package,pin,project,_=fixture(tmp_path)
    args=['--package',str(package),'--package-sha256',pin,'--project',str(project),'--confirm-isolated-project']
    with pytest.raises(SystemExit) as exc: main(['install',*args])
    assert exc.value.code==2
    assert main(['install',*args,'--confirm-change'])==0
    assert json.loads(capsys.readouterr().out)['operation']=='install'
    assert main(['review',*args])==0
    assert json.loads(capsys.readouterr().out)['operation']=='review'


def test_invalid_lineage_and_overlapping_package_never_write_project(tmp_path):
    package,pin,project,_=fixture(tmp_path)
    with pytest.raises(ValueError,match='separate directory'): call('install',package,pin,tmp_path)
    m=staging.load(package/'manifest.json');m.pop('package_sha256');m.pop('packet_sha256')
    bad=staging.sha(staging.canonical(m));(package/'manifest.json').write_bytes(staging.canonical({**m,'package_sha256':bad}))
    with pytest.raises(ValueError,match='lineage'): call('install',package,bad,project)
    assert not (project/'.claude').exists()
