"""Настоящие git pull и пересоздание Docker с прежним томом, без боевых данных.

RUN_DOCKER_PRESET_TESTS=1 PRESET_TEST_IMAGE=al-web-video-encoder:preset-editor-qa
python -m pytest -q tests/test_preset_persistence.py
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATE = '''
from pathlib import Path
import shutil, tempfile, yaml
from app.models.presets import preset_manager
from app.preset_migration import preview, apply
with tempfile.TemporaryDirectory() as temporary:
    source = Path(temporary) / 'legacy'
    shutil.copytree(preset_manager.service.builtin_dir, source)
    for relative, changes in [('video/hevc_remux.yaml', {'crf':'17', 'name':'Мой старый YAML'}),
                              ('audio/opus.yaml', {'bitrate':'192k'})]:
        path = source / relative
        values = yaml.safe_load(path.read_text())
        values.update(changes)
        path.write_text(yaml.safe_dump(values, allow_unicode=True))
    report = preview(preset_manager.service, source)
    apply(preset_manager.service, source, report['preview_digest'], report['catalog_revision'])
'''
SEED = '''
from app.models.presets import preset_manager
import json
service = preset_manager.service
definition = service.detail('video', 'hevc_auto_level_rip')['definition']
definition.update(name='Мой HEVC', crf='19')
custom = service.create('video', definition, preset_manager.catalog_revision, True)
definition.update(name='Моя версия стандартного', crf='21')
service.update('video', 'hevc_auto_level_rip', definition, preset_manager.catalog_revision)
service.set_defaults({'audio':'opus', 'subtitles':'none', 'data':'none'}, preset_manager.catalog_revision)
print(json.dumps({'id':custom['id'], 'revision':preset_manager.catalog_revision}))
'''
READ = '''
from app.models.presets import preset_manager
import json, hashlib
s = preset_manager.snapshot()
service = preset_manager.service
print(json.dumps({'id':s.default_video_id, 'custom':s.get_video(s.default_video_id).to_dict(),
 'defaults':{layer:getattr(s, 'default_'+layer+'_id') for layer in ('video','audio','subtitles','data')},
 'override':s.get_video('hevc_auto_level_rip').to_dict(),
 'builtin':s.get_video('hevc_rip').to_dict(),
 'shipped_crf':service.detail('video','hevc_rip')['current_builtin']['crf'],
 'legacy_video':s.get_video('hevc_remux').to_dict(),
 'legacy_audio':s.get_audio('opus').to_dict(),
 'migrations':service.export()['migrations'],
 'archive_sha':{str(p.relative_to(service.store.path.parent)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (service.store.path.parent/'legacy_archives').rglob('*') if p.is_file()},
 'changed':service.detail('video','hevc_auto_level_rip')['builtin_changed'],
 'revision':s.catalog_revision,
 'store_sha':hashlib.sha256(service.store.path.read_bytes()).hexdigest()}))
'''


def run(*command, cwd=None, env=None, timeout=60, input=None):
    result = subprocess.run(command, cwd=cwd, env=env, input=input, capture_output=True,
                            text=True, timeout=timeout)
    assert result.returncode == 0, f'{command[0]} failed: {result.stderr}\n{result.stdout}'
    return result.stdout.strip()


def assert_preserved(before, after, migrated=False):
    assert after['id'] == before['id']
    assert after['custom'] == before['custom']
    assert after['override'] == before['override']
    assert after['store_sha'] == before['store_sha']
    assert after['defaults'] == before['defaults']
    assert after['shipped_crf'] == '29'
    assert after['builtin']['crf'] == ('23' if migrated else '29')
    assert after['legacy_video'] == before['legacy_video']
    assert after['legacy_audio'] == before['legacy_audio']
    assert after['migrations'] == before['migrations']
    assert after['archive_sha'] == before['archive_sha']
    if migrated:
        assert after['migrations'] and after['archive_sha']
        assert after['legacy_video']['crf'] == '17'
        assert after['legacy_audio']['bitrate'] == '192k'
    assert after['revision'] != before['revision']
    assert after['changed'] is True


@pytest.mark.skipif(shutil.which('git') is None, reason='Для проверки git pull требуется Git')
@pytest.mark.parametrize('migrated', [False, True], ids=['editor', 'legacy-migration'])
def test_real_git_pull_keeps_user_catalog_and_updates_only_builtin(tmp_path, migrated):
    upstream, publisher, checkout = [tmp_path / name for name in ('upstream.git', 'publisher', 'checkout')]
    run('git', 'init', '--bare', str(upstream))
    publisher.mkdir()
    run('git', 'init', cwd=publisher)
    run('git', 'config', 'user.email', 'preset-tests@example.invalid', cwd=publisher)
    run('git', 'config', 'user.name', 'Preset tests', cwd=publisher)
    shutil.copytree(ROOT / 'app', publisher / 'app', ignore=shutil.ignore_patterns('__pycache__'))
    (publisher / '.gitignore').write_text('settings/\n__pycache__/\n*.pyc\n')
    run('git', 'add', '.', cwd=publisher)
    run('git', 'commit', '-m', 'initial preset delivery', cwd=publisher)
    run('git', 'remote', 'add', 'origin', str(upstream), cwd=publisher)
    run('git', 'push', 'origin', 'HEAD', cwd=publisher)
    run('git', 'clone', str(upstream), str(checkout))
    env = dict(os.environ, USER_PRESETS_PATH=str(checkout / 'settings/presets/catalog.json'),
               PYTHONPATH=str(checkout))
    run(sys.executable, '-c', (MIGRATE if migrated else '') + SEED, cwd=checkout, env=env)
    before = json.loads(run(sys.executable, '-c', READ, cwd=checkout, env=env))
    for name, crf in [('hevc_auto_level_rip', '27'), ('hevc_rip', '29')]:
        path = publisher / f'app/builtin_presets/video/{name}.yaml'
        path.write_text(path.read_text().replace('crf: "23"', f'crf: "{crf}"'))
    run('git', 'add', 'app/builtin_presets', cwd=publisher)
    run('git', 'commit', '-m', 'new builtin settings', cwd=publisher)
    run('git', 'push', 'origin', 'HEAD', cwd=publisher)
    run('git', 'pull', '--ff-only', cwd=checkout)
    after = json.loads(run(sys.executable, '-c', READ, cwd=checkout, env=env))
    assert_preserved(before, after, migrated)
    assert run('git', 'status', '--porcelain', cwd=checkout) == ''


@pytest.mark.skipif(os.getenv('RUN_DOCKER_PRESET_TESTS') != '1', reason='Требуется Docker и RUN_DOCKER_PRESET_TESTS=1')
@pytest.mark.parametrize('storage_kind', ['volume', 'bind'])
@pytest.mark.parametrize('migrated', [False, True], ids=['editor', 'legacy-migration'])
def test_recreated_container_with_new_image_keeps_same_settings_volume(tmp_path, storage_kind, migrated):
    image = os.environ.get('PRESET_TEST_IMAGE', 'al-web-video-encoder:preset-editor-qa')
    suffix = uuid.uuid4().hex[:12]
    volume = f'preset-editor-test-{suffix}'
    updated = f'preset-editor-test-updated:{suffix}'
    if storage_kind == 'volume':
        run('docker', 'volume', 'create', volume)
        mount = f'type=volume,source={volume},target=/app/settings'
    else:
        settings = tmp_path / 'settings'
        settings.mkdir()
        mount = f'type=bind,source={settings},target=/app/settings'
    try:
        def container(code, source=image):
            output = run('docker', 'run', '--rm', '--entrypoint', 'python', '--mount',
                         mount, source, '-c', code)
            return json.loads(output)
        seed = container((MIGRATE if migrated else '') + SEED)
        before = container(READ)
        assert before['id'] == seed['id']
        patch_code = "from pathlib import Path; " + '; '.join(
            f"p=Path('/app/app/builtin_presets/video/{name}.yaml'); p.write_text(p.read_text().replace('crf: \\\"23\\\"', 'crf: \\\"{crf}\\\"'))"
            for name, crf in [('hevc_auto_level_rip', '27'), ('hevc_rip', '29')])
        # JSON form of RUN avoids shell quoting; source image is built from real Dockerfile.
        dockerfile = f'FROM {image}\nRUN ' + json.dumps(['python', '-c', patch_code]) + '\n'
        run('docker', 'build', '-t', updated, '-f', '-', str(tmp_path), input=dockerfile, timeout=180)
        after = container(READ, updated)
        assert_preserved(before, after, migrated)
        assert container(READ)['custom'] == before['custom']
    finally:
        if storage_kind == 'volume':
            run('docker', 'volume', 'rm', volume)
        subprocess.run(['docker', 'image', 'rm', updated], capture_output=True)
