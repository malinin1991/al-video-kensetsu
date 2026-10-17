"""Full overrides, defaults, неизменяемые снимки и обновление поставки."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
import json
from pathlib import Path
import shutil

import pytest
import yaml

from app.models.presets import CatalogPresetManager, preset_manager
from app.preset_catalog import BUILTIN_DIR, CatalogError, PresetCatalog
from app.preset_schema import empty_state, normalize_definition, parse_json


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr('app.preset_catalog.available', lambda *args: True)
    return PresetCatalog(user_path=tmp_path / 'catalog.json')


def video(name='Мой пресет', **overrides):
    return dict(name=name, description='', video_codec='libx265', output_format='matroska', **overrides)


def test_clean_install_is_read_only_and_same_as_legacy(catalog):
    snapshot = catalog.snapshot()
    assert snapshot.resolve() == preset_manager.resolve()
    assert not catalog.store.path.exists()
    with pytest.raises(TypeError):
        snapshot.videos['x'] = snapshot.get_video('hevc_rip')
    with pytest.raises(FrozenInstanceError):
        snapshot.get_video('hevc_rip').name = 'x'
    with pytest.raises(AttributeError):
        snapshot.default_video_id = 'hevc_remux'


@pytest.mark.parametrize('layer,definition', [
    ('video', video()),
    ('audio', {'name': 'No audio', 'codec': None, 'audio_map': None}),
    ('subtitles', {'name': 'My subs', 'subtitle_codec': 'copy', 'subtitle_map': '0:s?'}),
    ('data', {'name': 'My attachments', 'data_codec': 'copy', 'data_map': '0:t?'}),
])
def test_create_and_restart_preserves_all_layers(catalog, layer, definition):
    created = catalog.create(layer, definition, catalog.snapshot().catalog_revision, True)
    assert created['id'].startswith('user_')
    assert created['source'] == 'custom'
    restored = PresetCatalog(user_path=catalog.store.path)
    assert restored.snapshot().catalog_revision == catalog.snapshot().catalog_revision
    assert restored.detail(layer, created['id'])['definition'] == created['definition']
    assert restored.describe()['defaults'][layer] == created['id']


def test_no_audio_semantics_follow_definition_not_id(catalog):
    created = catalog.create('audio', {'name': 'Silent', 'codec': None, 'audio_map': None}, catalog.snapshot().catalog_revision)
    params = catalog.snapshot().resolve(audio_id=created['id'])
    assert params['audio_map'] is None
    assert params['subtitle_map'] is None
    assert params['data_map'] is None


def test_full_override_does_not_inherit_optional_fields_and_reset(catalog):
    old = catalog.snapshot()
    detail = catalog.update('video', 'hevc_rip', video('Own'), old.catalog_revision)
    assert detail['definition']['preset'] is None
    assert catalog.snapshot().resolve('hevc_rip')['x265_params'] is None
    assert old.resolve('hevc_rip')['x265_params'] == 'level=5.1:ref=6'
    base = detail['base_snapshot']
    detail = catalog.update('video', 'hevc_rip', video('Own 2', crf=18), catalog.snapshot().catalog_revision)
    assert detail['base_snapshot'] == base
    catalog.set_defaults({'video': 'hevc_rip'}, catalog.snapshot().catalog_revision)
    reset = catalog.reset('video', 'hevc_rip', catalog.snapshot().catalog_revision)
    assert reset['source'] == 'builtin'
    assert reset['default']
    revision = catalog.snapshot().catalog_revision
    catalog.reset('video', 'hevc_rip', revision)
    assert catalog.snapshot().catalog_revision == revision


def test_delete_default_requires_atomic_replacement(catalog):
    created = catalog.create('video', video(), catalog.snapshot().catalog_revision, True)
    revision = catalog.snapshot().catalog_revision
    with pytest.raises(CatalogError) as caught:
        catalog.delete('video', created['id'], revision)
    assert caught.value.status == 409
    assert catalog.snapshot().catalog_revision == revision
    catalog.delete('video', created['id'], revision, 'hevc_remux')
    assert catalog.describe()['defaults']['video'] == 'hevc_remux'
    with pytest.raises(CatalogError):
        catalog.delete('video', 'hevc_rip', catalog.snapshot().catalog_revision)


def test_builtin_update_preserves_whole_override_without_writing_user_store(tmp_path):
    builtin = tmp_path / 'builtin'
    shutil.copytree(BUILTIN_DIR, builtin)
    service = PresetCatalog(builtin_dir=builtin, user_path=tmp_path / 'settings' / 'catalog.json')
    saved = service.update('video', 'hevc_rip', video('Own', crf=19), service.snapshot().catalog_revision)
    before = service.store.path.read_bytes()
    path = builtin / 'video' / 'hevc_rip.yaml'
    payload = yaml.safe_load(path.read_text())
    payload.update(crf=11, name='New supplied')
    path.write_text(yaml.safe_dump(payload))
    fresh = PresetCatalog(builtin_dir=builtin, user_path=service.store.path)
    assert fresh.detail('video', 'hevc_rip')['definition'] == saved['definition']
    assert fresh.detail('video', 'hevc_rip')['builtin_changed']
    assert fresh.snapshot().catalog_revision != service.snapshot().catalog_revision
    assert service.store.path.read_bytes() == before
    path.unlink()
    orphan = PresetCatalog(builtin_dir=builtin, user_path=service.store.path)
    assert orphan.detail('video', 'hevc_rip')['orphaned']
    assert not orphan.detail('video', 'hevc_rip')['actions']['reset']
    assert orphan.snapshot().resolve('hevc_rip')['crf'] == '19'


def test_concurrent_compare_and_swap_has_one_winner(catalog):
    revision = catalog.snapshot().catalog_revision
    def save(name):
        try:
            return catalog.create('video', video(name), revision)['id']
        except CatalogError as exc:
            return exc.status
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(save, ['A', 'B']))
    assert sum(isinstance(r, str) for r in results) == 1
    assert 412 in results
    assert len(catalog.export()['entries']) == 1


def test_backup_retention_failure_cannot_hide_or_erase_a_committed_preset(catalog, monkeypatch):
    original_stat = Path.stat
    backups = catalog.store.path.parent / 'backups'

    def missing_backup(path, *args, **kwargs):
        if path.parent == backups:
            raise FileNotFoundError('backup disappeared before retention stat')
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'stat', missing_backup)
    first = catalog.create('video', video('First committed'), catalog.snapshot().catalog_revision)
    assert first['id'] in catalog.snapshot().videos
    assert catalog.export() == catalog.store.load()
    second = catalog.create('video', video('Second committed'), catalog.snapshot().catalog_revision)
    expected = {first['id'], second['id']}
    assert {entry['id'] for entry in catalog.export()['entries']} == expected
    assert {entry['id'] for entry in catalog.store.load()['entries']} == expected


def test_readers_see_complete_snapshots(catalog):
    def write():
        for i in range(15):
            catalog.update('video', 'hevc_rip', video(str(i), crf=i), catalog.snapshot().catalog_revision)
    def read():
        for _ in range(150):
            snapshot = catalog.snapshot()
            assert len(snapshot.videos) == 3
            assert snapshot.resolve()['video_codec'] == 'libx265'
            assert len(snapshot.selection_snapshot()['layers']) == 4
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(write)] + [pool.submit(read) for _ in range(3)]
        for f in futures:
            f.result()


def test_corrupt_startup_blocks_and_reload_keeps_last_valid_snapshot(tmp_path):
    path = tmp_path / 'catalog.json'
    path.write_text('broken')
    service = PresetCatalog(user_path=path)
    assert not service.ready
    with pytest.raises(CatalogError) as caught:
        service.snapshot()
    assert caught.value.status == 503
    path.unlink()
    service.reload()
    previous = service.snapshot()
    path.write_text('broken again')
    service.reload()
    assert service.snapshot() is previous
    assert service.error
    assert path.read_text() == 'broken again'


def test_unknown_fields_duplicates_and_invalid_values():
    for definition in [video(crf=True), video(crf=1.5), video(crf='NaN'), video(crf=52), video(video_map='1:v'), video(video_map='0:v -i /tmp/a'), video(x265_params='a\n'), video(preset='invented'), video(extra=1)]:
        with pytest.raises(CatalogError):
            normalize_definition('video', definition)
    with pytest.raises(CatalogError):
        parse_json('{"a":1,"a":2}')
    for value in [{'name': 'Copy', 'codec': 'copy', 'audio_map': '0:a?', 'bitrate': '160k'}, {'name': 'Audio', 'codec': 'aac', 'audio_map': None}, {'name': 'Audio', 'codec': 'aac', 'audio_map': '0:a?', 'vbr': 'true'}]:
        with pytest.raises(CatalogError):
            normalize_definition('audio', value)
    normalized = normalize_definition('audio', {'name': 'Opus', 'codec': 'opus', 'audio_map': '0:a?', 'sample_rate': 48000, 'bitrate': 160})
    assert normalized['codec'] == 'libopus'
    assert normalized['bitrate'] == '160k'


def test_none_invariants_and_unavailable_default(catalog, monkeypatch):
    with pytest.raises(CatalogError):
        catalog.update('audio', 'none', {'name': 'Changed', 'codec': 'aac', 'audio_map': '0:a?'}, catalog.snapshot().catalog_revision)
    monkeypatch.setattr('app.preset_catalog.available', lambda layer, d: d.get('codec') != 'libfdk_aac')
    with pytest.raises(CatalogError) as caught:
        catalog.set_defaults({'audio': 'aac_fdk'}, catalog.snapshot().catalog_revision)
    assert caught.value.code == 'codec_unavailable'
    assert catalog.update('audio', 'aac_fdk', {'name': 'FDK', 'codec': 'libfdk_aac', 'audio_map': '0:a?'}, catalog.snapshot().catalog_revision)


def test_import_round_trip_preview_and_noop(catalog):
    catalog.create('video', video(), catalog.snapshot().catalog_revision)
    exported = catalog.export()
    revision = catalog.snapshot().catalog_revision
    report = catalog.preview_import(exported, 'replace_user_catalog')
    assert catalog.snapshot().catalog_revision == revision
    catalog.import_catalog(exported, 'replace_user_catalog', report['preview_digest'], revision)
    assert catalog.snapshot().catalog_revision == revision
    altered = json.loads(json.dumps(exported))
    altered['entries'][0]['definition']['name'] = 'Other'
    report = catalog.preview_import(altered, 'merge')
    assert report['conflicts']
    with pytest.raises(CatalogError) as caught:
        catalog.import_catalog(altered, 'merge', report['preview_digest'], revision)
    assert caught.value.status == 409
    replacement = catalog.preview_import(altered, 'replace_user_catalog')
    catalog.import_catalog(altered, 'replace_user_catalog', replacement['preview_digest'], revision)
    assert catalog.export()['entries'][0]['definition']['name'] == 'Other'
    assert catalog.export()['revision'] != exported['revision']


def test_stale_preview_and_missing_defaults_rejected(catalog):
    exported = catalog.export()
    preview = catalog.preview_import(exported, 'merge')
    catalog.create('video', video(), catalog.snapshot().catalog_revision)
    with pytest.raises(CatalogError) as caught:
        catalog.import_catalog(exported, 'merge', preview['preview_digest'], catalog.snapshot().catalog_revision)
    assert caught.value.code == 'preview_conflict'
    exported['defaults']['video'] = 'absent'
    with pytest.raises(CatalogError):
        catalog.preview_import(exported, 'replace_user_catalog')


def test_facade_follows_configured_store(monkeypatch, tmp_path):
    facade = CatalogPresetManager()
    monkeypatch.setenv('USER_PRESETS_PATH', str(tmp_path / 'one.json'))
    facade.service.create('video', video('One'), facade.catalog_revision)
    assert len(facade.videos) == 4
    monkeypatch.setenv('USER_PRESETS_PATH', str(tmp_path / 'two.json'))
    assert len(facade.videos) == 3
    monkeypatch.setenv('USER_PRESETS_PATH', str(tmp_path / 'one.json'))
    assert len(facade.videos) == 4


def test_commit_unknown_reconciles_snapshot_with_disk(catalog, monkeypatch):
    from app import preset_store
    real_fsync = preset_store.fsync_directory
    def fail_main(directory):
        if Path(directory) == catalog.store.path.parent:
            raise OSError('directory fsync failed')
        return real_fsync(directory)
    monkeypatch.setattr(preset_store, 'fsync_directory', fail_main)
    old_revision = catalog.snapshot().catalog_revision
    with pytest.raises(CatalogError) as caught:
        catalog.create('video', video('Committed despite fsync failure'), old_revision)
    assert caught.value.code == 'save_result_unknown'
    assert catalog.snapshot().catalog_revision != old_revision
    assert len(catalog.snapshot().videos) == 4
    assert catalog.export() == catalog.store.load()


def test_last_snapshot_survives_failed_reload_without_overwriting_corruption(catalog):
    before = catalog.snapshot()
    catalog.store.path.write_text('corrupt')
    catalog.reload()
    assert catalog.snapshot() is before
    with pytest.raises(CatalogError) as caught:
        catalog.create('video', video(), before.catalog_revision)
    assert caught.value.status == 503
    assert catalog.store.path.read_text() == 'corrupt'


def test_legacy_custom_collision_is_visible_and_deletable(catalog):
    imported = empty_state()
    imported['entries'].append({'layer': 'video', 'id': 'hevc_rip', 'kind': 'custom',
                               'definition': video('Legacy custom'), 'base_snapshot': None, 'base_fingerprint': None})
    preview = catalog.preview_import(imported, 'merge')
    catalog.import_catalog(imported, 'merge', preview['preview_digest'], catalog.snapshot().catalog_revision)
    detail = catalog.detail('video', 'hevc_rip')
    assert detail['source_collision']
    assert detail['actions']['delete']
    assert not detail['actions']['reset']
    assert catalog.snapshot().resolve('hevc_rip')['crf'] is None
    catalog.delete('video', 'hevc_rip', catalog.snapshot().catalog_revision)
    assert catalog.detail('video', 'hevc_rip')['source'] == 'builtin'
