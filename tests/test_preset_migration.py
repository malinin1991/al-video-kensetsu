"""Offline legacy import archives exact YAML and never imports app.main."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import os

import pytest
import yaml

from app.preset_catalog import BUILTIN_DIR, PresetCatalog
from app.preset_migration import apply, preview, restore
from app.preset_schema import CatalogError, empty_state


@pytest.fixture
def source(tmp_path):
    directory = tmp_path / 'legacy'
    shutil.copytree(BUILTIN_DIR, directory)
    old = directory / 'video' / 'hevc_rip.yaml'
    values = yaml.safe_load(old.read_text())
    values.update(crf=18, name='Legacy changed')
    old.write_text(yaml.safe_dump(values))
    return directory


def test_preview_apply_archives_and_is_idempotent(source, tmp_path, monkeypatch):
    monkeypatch.setattr('app.preset_catalog.available', lambda *args: True)
    service = PresetCatalog(user_path=tmp_path / 'settings' / 'catalog.json')
    report = preview(service, source)
    assert not service.store.path.exists()
    assert not report['conflicts']
    result = apply(service, source, report['preview_digest'], report['catalog_revision'])
    assert service.snapshot().resolve('hevc_rip')['crf'] == '18'
    assert service.detail('video', 'hevc_rip')['base_unknown']
    assert service.export()['defaults']['video'] == 'hevc_auto_level_rip'
    archive = Path(result['archive'])
    for relative in report['sources']:
        assert (archive / relative).read_bytes() == (source / relative).read_bytes()
    previous = service.store.path.read_bytes()
    repeated = apply(service, source, report['preview_digest'], report['catalog_revision'])
    assert repeated['already_applied']
    assert service.store.path.read_bytes() == previous


def test_changed_source_and_conflict_leave_catalog_unmodified(source, tmp_path, monkeypatch):
    monkeypatch.setattr('app.preset_catalog.available', lambda *args: True)
    service = PresetCatalog(user_path=tmp_path / 'catalog.json')
    report = preview(service, source)
    old = source / 'video' / 'hevc_rip.yaml'
    payload = yaml.safe_load(old.read_text())
    payload['crf'] = 17
    old.write_text(yaml.safe_dump(payload))
    with pytest.raises(CatalogError) as caught:
        apply(service, source, report['preview_digest'], report['catalog_revision'])
    assert caught.value.code == 'preview_conflict'
    assert not service.store.path.exists()
    service.update('video', 'hevc_rip', {'name': 'Own', 'video_codec': 'libx265', 'output_format': 'matroska'}, service.snapshot().catalog_revision)
    before = service.store.path.read_bytes()
    report = preview(service, source)
    assert report['conflicts']
    with pytest.raises(CatalogError):
        apply(service, source, report['preview_digest'], report['catalog_revision'])
    assert service.store.path.read_bytes() == before


def test_source_changed_inside_apply_is_not_imported_without_new_preview(source, tmp_path, monkeypatch):
    import app.preset_migration as migration
    service = PresetCatalog(user_path=tmp_path / 'catalog.json')
    report = preview(service, source)
    original_candidate = migration.migration_candidate
    calls = 0

    def change_after_internal_preview(service, directory):
        nonlocal calls
        result = original_candidate(service, directory)
        calls += 1
        if calls == 1:
            path = source / 'video' / 'hevc_rip.yaml'
            payload = yaml.safe_load(path.read_text())
            payload['crf'] = 13
            path.write_text(yaml.safe_dump(payload))
        return result

    monkeypatch.setattr(migration, 'migration_candidate', change_after_internal_preview)
    with pytest.raises(CatalogError) as caught:
        apply(service, source, report['preview_digest'], report['catalog_revision'])
    assert caught.value.code == 'preview_conflict'
    assert not service.store.path.exists()
    assert not service.export()['entries']


@pytest.mark.parametrize('invalid', ['id: evil\nname: A\nvideo_codec: libx265\noutput_format: matroska\ndefault: true\n', 'id: bad\nid: bad2\n', '!!python/object/apply:builtins.str [unsafe]'])
def test_invalid_sources_have_no_partial_writes(source, tmp_path, invalid):
    service = PresetCatalog(user_path=tmp_path / 'catalog.json')
    (source / 'video' / 'invalid.yaml').write_text(invalid)
    with pytest.raises(CatalogError):
        preview(service, source)
    assert not service.store.path.exists()


def test_preview_reports_all_invalid_files_without_touching_existing_catalog(source, tmp_path):
    service = PresetCatalog(user_path=tmp_path / 'catalog.json')
    service.create('video', {'name': 'Keep', 'video_codec': 'libx265', 'output_format': 'matroska'},
                   service.snapshot().catalog_revision)
    before = service.store.path.read_bytes()
    (source / 'video' / 'bad-quality.yaml').write_text(
        'id: bad_quality\nname: Bad\nvideo_codec: libx265\noutput_format: matroska\ncrf: 99\n')
    (source / 'audio' / 'bad-rate.yaml').write_text(
        'id: bad_rate\nname: Bad\ncodec: aac\nsample_rate: 2\n')
    (source / 'data' / 'bad-encoding.yaml').write_bytes(b'\xff')
    with pytest.raises(CatalogError) as caught:
        preview(service, source)
    assert caught.value.code == 'legacy_validation_failed'
    errors = {entry['path']: entry for entry in caught.value.fields['sources']}
    assert set(errors) == {'video/bad-quality.yaml', 'audio/bad-rate.yaml', 'data/bad-encoding.yaml'}
    assert 'crf' in errors['video/bad-quality.yaml']['fields']
    assert 'sample_rate' in errors['audio/bad-rate.yaml']['fields']
    assert service.store.path.read_bytes() == before


def test_cli_reports_all_source_errors_and_refuses_apply(source, tmp_path):
    (source / 'video' / 'bad.yaml').write_text('id: [broken')
    (source / 'audio' / 'bad.yaml').write_text('id: duplicate\nid: duplicate\n')
    env = dict(os.environ, USER_PRESETS_PATH=str(tmp_path / 'data' / 'catalog.json'))
    command = [sys.executable, '-m', 'app.preset_migration']
    for arguments in (['preview'], ['apply', '--preview-digest', 'stale', '--if-match', 'stale']):
        result = subprocess.run(command + arguments + ['--source', str(source)], env=env,
                                capture_output=True, text=True)
        assert result.returncode == 1
        report = json.loads(result.stderr)
        assert report['code'] == 'legacy_validation_failed'
        assert {entry['path'] for entry in report['fields']['sources']} == {'video/bad.yaml', 'audio/bad.yaml'}
        assert not result.stdout
        assert not (tmp_path / 'data').exists()


def test_restore_validates_digest_and_backup_before_replacing(tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text('corrupt')
    service = PresetCatalog(user_path=path)
    backup = tmp_path / 'backup.json'
    backup.write_text(json.dumps(empty_state()))
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(CatalogError):
        restore(service, backup, 'wrong')
    assert path.read_text() == 'corrupt'
    result = restore(service, backup, digest)
    assert json.loads(path.read_text())['revision'] != 'empty-v1'
    assert Path(result['preserved_original']).read_text() == 'corrupt'
    assert PresetCatalog(user_path=path).ready


def test_cli_runs_without_main_import_or_runtime_side_effects(source, tmp_path):
    env = dict(os.environ, USER_PRESETS_PATH=str(tmp_path / 'data' / 'catalog.json'), LOG_DIR=str(tmp_path / 'no-logs'))
    command = [sys.executable, '-m', 'app.preset_migration']
    result = subprocess.run(command + ['preview', '--source', str(source)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert not (tmp_path / 'data').exists()
    applied = subprocess.run(command + ['apply', '--source', str(source), '--preview-digest', report['preview_digest'], '--if-match', report['catalog_revision']], env=env, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    assert not (tmp_path / 'no-logs').exists()
