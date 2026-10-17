"""Каталог применяется один раз на загрузку, история не зависит от живых пресетов."""
import io
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.main import app
from app.models.job import Job, JobStatus
from app.models.presets import preset_manager
from app.job_store import get_job_store
import app.routes as routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv('UPLOADS_DIR', str(tmp_path / 'uploads'))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: MagicMock())
    routes.jobs_storage.clear()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client
    routes.jobs_storage.clear()


def revision(client):
    response = client.get('/api/presets')
    assert response.status_code == 200, response.json
    return response.json['catalog_revision']


def create_video(client, *, name='Мой профиль', crf='19', default=False):
    definition = preset_manager.service.detail('video', 'hevc_auto_level_rip')['definition']
    definition.update(name=name, crf=crf)
    response = client.post('/api/preset-editor/video', json={'definition': definition, 'set_default': default},
                           headers={'If-Match': f'"{revision(client)}"'})
    assert response.status_code == 201, response.json
    return response.json


def test_default_applies_everywhere_without_restart_and_label_is_frozen(client, tmp_path, monkeypatch):
    created = create_video(client, default=True)
    source = tmp_path / 'library'
    source.mkdir()
    (source / 'episode.mkv').write_bytes(b'input')
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', str(source))
    upload = client.post('/api/upload', data={'file': (io.BytesIO(b'input'), 'episode.mp4')})
    internal = client.post('/api/internal/encode', json={'path': str(source / 'episode.mkv')})
    for response in (upload, internal):
        assert response.status_code == 200, response.json
        assert response.json['job']['video_id'] == created['id']
        assert response.json['job']['ffmpeg_params']['crf'] == '19'
    job = routes.get_job(upload.json['job']['id'])
    before = job.to_record()
    definition = dict(created['definition'], name='Переименован', crf='35')
    changed = client.put(f'/api/preset-editor/video/{created["id"]}', json={'definition': definition},
                         headers={'If-Match': f'"{revision(client)}"'})
    assert changed.status_code == 200
    deleted = client.delete(f'/api/preset-editor/video/{created["id"]}', json={'replacement_default_id': 'hevc_auto_level_rip'},
                            headers={'If-Match': f'"{revision(client)}"'})
    assert deleted.status_code == 200, deleted.json
    recovered = Job.from_record(before)
    assert job.ffmpeg_params['crf'] == recovered.ffmpeg_params['crf'] == '19'
    assert job.display_label() == recovered.display_label() == 'Мой профиль / Copy'


@pytest.mark.parametrize('vbr,expected_mode', [(None, 'on'), (False, 'off'), (True, 'on')])
def test_custom_video_edit_preserves_auto_or_explicit_opus_vbr(client, vbr, expected_mode):
    from app.ffmpeg_worker import FFmpegWorker
    audio = preset_manager.service.create('audio', {'name': 'Opus', 'codec': 'libopus',
        'audio_map': '0:a?', 'vbr': vbr}, revision(client))
    resolved = client.get('/api/presets/resolve', query_string={
        'video_id': 'hevc_auto_level_rip', 'audio_id': audio['id']}).json
    # Главная форма отправляет auto как отсутствие поля, explicit — boolean string.
    fields = {key: str(value).lower() if isinstance(value, bool) else str(value)
              for key, value in resolved['ffmpeg_params'].items() if value is not None}
    fields.update(video_id='hevc_auto_level_rip', audio_id=audio['id'], preset_modified='true',
                  catalog_revision=resolved['catalog_revision'], crf='20')
    response = client.post('/api/upload', data={**fields, 'file': (io.BytesIO(b'input'), 'episode.mp4')})
    assert response.status_code == 200, response.json
    params = response.json['job']['ffmpeg_params']
    assert params['crf'] == '20'
    assert params.get('audio_vbr') == (None if vbr is None else str(vbr).lower())
    command = []
    FFmpegWorker._append_audio_encode_options(object.__new__(FFmpegWorker), command, params, 'libopus')
    assert command[command.index('-vbr') + 1] == expected_mode


def chunk(client, number, fields=None, file_id='pinned'):
    return client.post('/api/upload', data={
        'file': (io.BytesIO(b'part'), 'chunk'), 'file_id': file_id,
        'chunk_number': str(number), 'total_chunks': '2', 'filename': 'episode.mp4', **(fields or {}),
    })


def test_chunk_uses_first_snapshot_even_after_preset_is_deleted(client, tmp_path):
    created = create_video(client)
    fields = {'video_id': created['id'], 'catalog_revision': revision(client)}
    assert chunk(client, 0, fields).status_code == 200
    removed = client.delete(f'/api/preset-editor/video/{created["id"]}', json={},
                            headers={'If-Match': f'"{revision(client)}"'})
    assert removed.status_code == 200
    completed = chunk(client, 1, fields)
    assert completed.status_code == 200, completed.json
    job = completed.json['job']
    assert job['video_id'] == created['id']
    assert job['ffmpeg_params']['crf'] == '19'
    assert job['preset_label'] == 'Мой профиль / Copy'
    assert job['preset_snapshot']['catalog_revision'] == fields['catalog_revision']


def test_new_stale_upload_has_no_disk_effect_and_old_chunks_survive_conflict(client, tmp_path):
    old = revision(client)
    create_video(client)
    stale = chunk(client, 0, {'catalog_revision': old})
    assert stale.status_code == 409 and stale.json['code'] == 'preset_catalog_changed'
    assert not (tmp_path / 'uploads').exists()
    assert chunk(client, 0).status_code == 200
    conflict = chunk(client, 1, {'cpu_threads': '3'})
    assert conflict.status_code == 409
    assert (tmp_path / 'uploads/temp_pinned/chunk_0000').read_bytes() == b'part'


def test_legacy_manifest_requires_explicit_restart_without_deleting_chunks(client, tmp_path):
    folder = tmp_path / 'uploads/temp_pinned'
    folder.mkdir(parents=True)
    (folder / 'upload.json').write_text(json.dumps({'filename': 'episode.mp4', 'total_chunks': 2}))
    (folder / 'chunk_0000').write_bytes(b'old')
    response = chunk(client, 1)
    assert response.status_code == 409 and response.json['code'] == 'upload_snapshot_missing'
    assert (folder / 'chunk_0000').read_bytes() == b'old'


def test_receipt_replay_precedes_catalog_validation(client, tmp_path, monkeypatch):
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'video.mkv').write_bytes(b'input')
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', str(source))
    payload = {'path': str(source / 'video.mkv'), 'request_id': 'stable-receipt', 'catalog_revision': revision(client)}
    accepted = client.post('/api/internal/encode', json=payload)
    assert accepted.status_code == 200
    create_video(client, default=True)
    assert client.post('/api/internal/encode', json=payload).json == accepted.json


def test_corrupt_store_blocks_new_upload_but_history_and_editor_page_are_available(client, tmp_path, monkeypatch):
    path = tmp_path / 'corrupt-catalog.json'
    path.write_text('{broken')
    monkeypatch.setenv('USER_PRESETS_PATH', str(path))
    assert client.get('/api/presets').status_code == 503
    assert client.get('/presets').status_code == 200
    assert client.get('/api/jobs').status_code == 200
    response = client.post('/api/upload', data={'file': (io.BytesIO(b'data'), 'video.mkv')})
    assert response.status_code == 503 and response.json['code'] == 'storage_corrupt'
    assert path.read_text() == '{broken'
    assert not (tmp_path / 'uploads').exists()


def test_legacy_recovery_never_resolves_current_presets(client, monkeypatch):
    job = Job(id='legacy', filename='video.mp4', original_path='missing', ffmpeg_params={'crf': '18'})
    record = job.to_record()
    record.pop('preset_snapshot')
    monkeypatch.setattr(preset_manager, 'snapshot', lambda: pytest.fail('history must not resolve'))
    recovered = Job.from_record(record)
    assert recovered.ffmpeg_params['crf'] == '18'
    assert 'название на момент создания не сохранено' in recovered.display_label()


def test_one_corrupt_job_does_not_break_other_history(client):
    store = get_job_store()
    good = Job(id='good', filename='ok.mkv', original_path='missing', status=JobStatus.DONE)
    store.insert_job(good)
    with store._connect() as db:
        db.execute('INSERT INTO jobs VALUES (?, ?)', ('broken', '{broken'))
    recovered = {job.id: job for job in store.recover()}
    assert recovered['good'].status == JobStatus.DONE
    assert recovered['broken'].status == JobStatus.ERROR
