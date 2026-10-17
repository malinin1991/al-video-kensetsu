"""Полные атомарные состояния, backup и диагностика повреждений."""
import json
from pathlib import Path
import uuid

import pytest

from app.preset_schema import CatalogError, empty_state
from app.preset_store import PresetStore


def state():
    result = empty_state()
    result['revision'] = str(uuid.uuid4())
    return result


def test_missing_store_is_stable_without_writes(tmp_path):
    store = PresetStore(tmp_path / 'presets' / 'catalog.json')
    assert store.load() == empty_state()
    assert store.load()['revision'] == 'empty-v1'
    assert not store.path.parent.exists()


def test_save_backups_include_initial_state_and_retention(tmp_path):
    store = PresetStore(tmp_path / 'catalog.json')
    previous = empty_state()
    for _ in range(23):
        candidate = state()
        store.save(previous, candidate)
        assert store.load() == candidate
        previous = candidate
    backups = list((tmp_path / 'backups').glob('*.json'))
    assert len(backups) == 20
    assert all(json.loads(p.read_text())['schema_version'] == 1 for p in backups)
    assert not list(tmp_path.glob('*.tmp'))


def test_first_backup_is_empty_valid_state(tmp_path):
    store = PresetStore(tmp_path / 'catalog.json')
    store.save(empty_state(), state())
    backup = next((tmp_path / 'backups').glob('*.json'))
    assert json.loads(backup.read_text()) == empty_state()


@pytest.mark.parametrize('contents', ['{bad', '{"schema_version":99}', '{"revision":1,"revision":2}'])
def test_corrupt_store_not_reinitialized(tmp_path, contents):
    path = tmp_path / 'catalog.json'
    path.write_text(contents)
    with pytest.raises(CatalogError) as caught:
        PresetStore(path).load()
    assert caught.value.status == 503
    assert path.read_text() == contents


def test_replace_failure_preserves_previous_file(tmp_path, monkeypatch):
    import app.preset_store as module
    store = PresetStore(tmp_path / 'catalog.json')
    old = state()
    store.save(empty_state(), old)
    replace = module.os.replace
    def fail_main(source, destination):
        if Path(destination) == store.path:
            raise OSError('disk full')
        return replace(source, destination)
    monkeypatch.setattr(module.os, 'replace', fail_main)
    with pytest.raises(CatalogError, match='disk full') as caught:
        store.save(old, state())
    assert caught.value.code == 'storage_unavailable'
    assert store.load() == old
    assert not list(tmp_path.glob('*.tmp'))


def test_fsync_after_replace_reports_unknown_result(tmp_path, monkeypatch):
    import app.preset_store as module
    store = PresetStore(tmp_path / 'catalog.json')
    candidate = state()
    original = module.fsync_directory
    def fail_main(path):
        if Path(path) == tmp_path:
            raise OSError('fsync failed')
        return original(path)
    monkeypatch.setattr(module, 'fsync_directory', fail_main)
    with pytest.raises(CatalogError) as caught:
        store.save(empty_state(), candidate)
    assert caught.value.code == 'save_result_unknown'
    assert store.load() == candidate


def test_backup_failure_prevents_main_write(tmp_path, monkeypatch):
    import app.preset_store as module
    store = PresetStore(tmp_path / 'catalog.json')
    def fail(*args):
        raise CatalogError('backup denied', 'storage_unavailable', 503)
    monkeypatch.setattr(module, 'atomic_write', fail)
    with pytest.raises(CatalogError, match='backup denied'):
        store.save(empty_state(), state())
    assert not store.path.exists()
