"""Release packaging tests: no installed plugins, network, services or devices."""
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bundle_builder', ROOT/'tools/build_plugin_bundles.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


@pytest.fixture
def source(tmp_path):
    root = tmp_path/'source'
    inputs = {name: b'guide\n' for name in builder.COMMON}
    platforms = {}
    for platform in builder.VERSIONS:
        prefix = {'codex':'codex/plugins/xueqiu-status', 'claude':'claude/plugins/snowball-companion',
                  'cursor':'cursor/snowball-companion'}[platform]
        manifest = prefix + {'codex':'/.codex-plugin/plugin.json','claude':'/.claude-plugin/plugin.json',
                             'cursor':'/.cursor-plugin/plugin.json'}[platform]
        inputs[manifest] = builder.encoded({'version':builder.VERSIONS[platform]})
        inputs[platform+'/README.md'] = b'platform\n'
        platforms[platform] = [manifest,platform+'/README.md']
    pet = 'codex/plugins/xueqiu-status/assets/pet/'
    extra = {pet+'catalog.json':builder.encoded({'default_character':'snowball','characters':[
        {'id':'snowball','manifest':'manifest.json'},{'id':'other','manifest':'other/manifest.json'}]}),
        pet+'manifest.json':b'{}',pet+'snowball.png':b'base',pet+'other/atlas.png':b'extra',
        'codex/install.sh':b'#!/bin/sh\ntrue\n'}
    inputs.update(extra);platforms['codex'] += list(extra)
    inputs[builder.MANIFEST] = builder.encoded({'schema':1,'common':sorted(builder.COMMON),'platforms':platforms})
    for name,data in inputs.items():
        p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(data)
    return root


def test_same_source_has_identical_zip_hashes_despite_mtime(source,tmp_path):
    for platform in builder.VERSIONS:
        first=builder.build(source,tmp_path/'a',platform)
        os.utime(source/(platform+'/README.md'),(1234567890,1234567890))
        second=builder.build(source,tmp_path/'b',platform)
        assert first==second
        assert builder.verify_archive(tmp_path/'a'/first['file'])==first['files']-1


def test_unlisted_private_state_and_engineering_files_never_enter_zip(source,tmp_path):
    paths=['codex/private-secret.json','codex/plugins/xueqiu-status/.env',
           'docs/OTA-DOWNLOAD-PLAN.md','firmware/main.c','cloud/worker.ts','web/App.tsx']
    for name in paths:
        p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_text('not an input')
    result=builder.build(source,tmp_path/'out','codex')
    with zipfile.ZipFile(tmp_path/'out'/result['file']) as z:
        assert not any(any(n.endswith(p) for p in paths) for n in z.namelist())


@pytest.mark.parametrize('name',['../outside','/absolute/file','codex/../private','codex/.env',
                               'codex/private.key','codex/user.sqlite','codex/qa/log.json','codex\\file'])
def test_private_or_traversal_path_is_rejected(name):
    with pytest.raises(ValueError):builder.validate_name(name)


@pytest.mark.parametrize('payload',[b'-----BEGIN PRIVATE KEY-----',b'/home/example/private/',
                                     b'access_token = "example_secret_value"'])
def test_private_content_is_rejected_without_echoing_value(source,payload):
    p=source/'codex/README.md';p.write_bytes(payload)
    with pytest.raises(ValueError) as e:builder.bundle_inputs(source,'codex')
    assert payload.decode() not in str(e.value)


def test_missing_file_or_symlink_fails_without_clobbering_old_zip(source,tmp_path):
    out=tmp_path/'out';first=builder.build(source,out,'codex');before=(out/first['file']).read_bytes()
    p=source/'codex/README.md';p.unlink()
    with pytest.raises(ValueError):builder.build(source,out,'codex')
    outside=tmp_path/'outside';outside.write_text('external')
    p.symlink_to(outside)
    with pytest.raises(ValueError):builder.build(source,out,'codex')
    assert (out/first['file']).read_bytes()==before
    assert outside.read_text()=='external'


def test_cross_platform_and_duplicate_sources_are_rejected(source):
    p=source/builder.MANIFEST;value=json.loads(p.read_text())
    value['platforms']['codex'].append('claude/README.md');p.write_bytes(builder.encoded(value))
    with pytest.raises(ValueError):builder.source_lists(source)
    value['platforms']['codex'].pop();value['platforms']['codex'].append('codex/README.md')
    p.write_bytes(builder.encoded(value))
    with pytest.raises(ValueError):builder.source_lists(source)


def test_core_filters_assets_catalog_and_checks_size(source,tmp_path,monkeypatch):
    files=builder.bundle_inputs(source,'codex',core=True)
    catalog=json.loads(files['codex/plugins/xueqiu-status/assets/pet/catalog.json'])
    assert [c['id'] for c in catalog['characters']]==['snowball']
    assert not any('/other/' in n for n in files)
    monkeypatch.setattr(builder,'CORE_LIMIT',10)
    with pytest.raises(ValueError):builder.build(source,tmp_path/'out','codex',core=True)
    assert not list((tmp_path/'out').glob('*.zip'))


def test_archive_verifier_rejects_changed_payload(source,tmp_path):
    result=builder.build(source,tmp_path/'out','codex');path=tmp_path/'out'/result['file']
    with zipfile.ZipFile(path) as z:items=[(i,z.read(i.filename)) for i in z.infolist()]
    with zipfile.ZipFile(path,'w') as z:
        for info,data in items:
            z.writestr(info,data+b'tampered' if info.filename.endswith('/codex/README.md') else data)
    with pytest.raises(ValueError):builder.verify_archive(path)


def test_real_core_extracts_installs_no_state_and_validates_snowball(tmp_path):
    result=builder.build(ROOT,tmp_path/'out','codex',core=True)
    assert result['bytes']<8_000_000
    with zipfile.ZipFile(tmp_path/'out'/result['file']) as z:z.extractall(tmp_path/'extract')
    package=tmp_path/'extract/Snowball-Codex-Core'
    script=package/'codex/plugins/xueqiu-status/scripts/pet_setup.py'
    private=tmp_path/'isolated-codex';env=dict(os.environ,CODEX_HOME=str(private),PYTHONDONTWRITEBYTECODE='1')
    reply=subprocess.run([sys.executable,str(script),'list'],capture_output=True,text=True,check=True,env=env)
    data=json.loads(reply.stdout)
    assert [c['id'] for c in data['characters']]==['snowball']
    assert data['characters'][0]['status']=='ready'
    assert not private.exists()
    assert os.stat(package/'codex/install.sh').st_size>0


def test_three_adapters_keep_separate_entrypoints_and_versions():
    for platform in builder.VERSIONS:builder.bundle_inputs(ROOT,platform)
    for platform,relative in [('claude','plugins/snowball-companion'),('cursor','snowball-companion')]:
        source=(ROOT/platform/relative/'scripts/run_hub.py').read_text()
        assert 'StatusHub(STATE_DIR, desktop=None)' in source
        assert 'activity_ticker' not in source
        doctor=(ROOT/platform/relative/'scripts/control.py').read_text()
        assert "'version': '"+builder.VERSIONS[platform]+"'" in doctor
    assert 'docs/OTA-DOWNLOAD-PLAN.md' not in builder.source_lists(ROOT)['common']
