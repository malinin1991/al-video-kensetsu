"""Batch upload / internal encode и валидация preset_id / FDK."""

from __future__ import annotations

import io
from unittest.mock import MagicMock

import pytest

from app.main import app
import app.routes as routes
from app.models.job import JobStatus
from app.models.presets import preset_manager


@pytest.fixture
def upload_env(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'app_settings.json'))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)
    monkeypatch.delenv('INTERNAL_SOURCE_ROOTS', raising=False)

    dummy = MagicMock()

    def fake_add_job(job):
        job.status = JobStatus.PLANNED

    dummy.add_job.side_effect = fake_add_job
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: dummy)
    routes.jobs_storage.clear()

    app.config['TESTING'] = True
    return uploads


@pytest.fixture
def client(upload_env):
    with app.test_client() as test_client:
        yield test_client


@pytest.fixture
def internal_env(tmp_path, monkeypatch):
    source_root = tmp_path / 'anilibria'
    uploads = tmp_path / 'uploads'
    source_root.mkdir()
    uploads.mkdir()
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', str(source_root))
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'app_settings.json'))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)

    dummy = MagicMock()

    def fake_add_job(job):
        job.status = JobStatus.PLANNED

    dummy.add_job.side_effect = fake_add_job
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: dummy)
    routes.jobs_storage.clear()
    app.config['TESTING'] = True
    return source_root, uploads


@pytest.fixture
def internal_client(internal_env):
    with app.test_client() as test_client:
        yield test_client


class TestPresetsApi:
    def test_presets_have_group_and_available(self, client, monkeypatch):
        monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
        response = client.get('/api/presets')
        assert response.status_code == 200
        data = response.get_json()
        ids = {p['id'] for p in data}
        assert 'default' in ids
        assert 'bd_default' in ids
        assert 'aac_fdk_audio' in ids
        assert 'anime_optimized' not in ids
        for p in data:
            assert 'group' in p
            assert 'available' in p
            if p['id'] in ('aac_fdk_audio', 'bd_aac_fdk_audio'):
                assert p['available'] is False
            else:
                assert p['available'] is True
        groups = [p['group'] for p in data]
        assert groups == sorted(groups, key=lambda g: 0 if g == 'rip' else 1)
        rip = [p for p in data if p['group'] == 'rip']
        assert rip[0]['id'] == 'default'
        remux = [p for p in data if p['group'] == 'remux']
        assert remux[0]['id'] == 'bd_default'


class TestPresetValidation:
    def test_unknown_preset_id_400(self, client):
        data = {
            'preset_id': 'no_such_preset',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        assert 'пресет' in response.get_json()['error'].lower()

    def test_fdk_preset_unavailable_400(self, client, monkeypatch):
        monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
        data = {
            'preset_id': 'aac_fdk_audio',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        assert 'libfdk_aac' in response.get_json()['error']

    def test_ambiguous_preset_name_400(self, internal_client, internal_env):
        source_root, _uploads = internal_env
        a = source_root / 'a.mkv'
        a.write_bytes(b'aaa')
        response = internal_client.post(
            '/api/internal/encode',
            json={'path': str(a), 'preset': 'По умолчанию'},
        )
        assert response.status_code == 400
        err = response.get_json()['error'].lower()
        assert 'неоднознач' in err
        assert 'default' in err
        assert 'bd_default' in err


class TestBatchUpload:
    def test_batch_files_creates_jobs(self, client):
        data = {
            'preset_id': 'default',
            'files': [
                (io.BytesIO(b'aaa'), 'a.mkv'),
                (io.BytesIO(b'bbb'), 'b.mkv'),
            ],
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['created'] == 2
        assert len(body['jobs']) == 2
        assert body['errors'] == []

    def test_batch_multiple_file_fields(self, client):
        data = {
            'preset_id': 'default',
            'file': [
                (io.BytesIO(b'aaa'), 'a.mkv'),
                (io.BytesIO(b'bbb'), 'b.mkv'),
            ],
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['created'] == 2
        assert len(body['jobs']) == 2

    def test_batch_partial_207(self, client):
        data = {
            'preset_id': 'default',
            'files': [
                (io.BytesIO(b'aaa'), 'a.mkv'),
                (io.BytesIO(b'xxx'), 'bad.txt'),
            ],
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 207
        body = response.get_json()
        assert body['created'] == 1
        assert len(body['errors']) == 1

    def test_batch_orphan_cleanup_on_exception(self, client, upload_env, monkeypatch):
        def boom(*_args, **_kwargs):
            raise RuntimeError('simulated failure after save')

        monkeypatch.setattr(routes, 'create_job_from_saved_file', boom)
        data = {
            'preset_id': 'default',
            'files': [(io.BytesIO(b'aaa'), 'orphan.mkv')],
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        body = response.get_json()
        assert body['created'] == 0
        assert len(body['errors']) == 1
        assert list(upload_env.iterdir()) == []


class TestBatchInternalEncode:
    def test_paths_batch(self, internal_client, internal_env):
        source_root, _uploads = internal_env
        a = source_root / 'a.mkv'
        b = source_root / 'b.mkv'
        a.write_bytes(b'aaa')
        b.write_bytes(b'bbb')
        response = internal_client.post(
            '/api/internal/encode',
            json={
                'paths': [str(a), str(b)],
                'preset_id': 'opus_audio',
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['created'] == 2
        assert len(body['jobs']) == 2
        assert body['jobs'][0]['preset_id'] == 'opus_audio'

    def test_single_element_paths_batch_shape(self, internal_client, internal_env):
        source_root, _uploads = internal_env
        a = source_root / 'only.mkv'
        a.write_bytes(b'only')
        response = internal_client.post(
            '/api/internal/encode',
            json={'paths': [str(a)], 'preset_id': 'default'},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['created'] == 1
        assert 'jobs' in body
        assert 'job' not in body
        assert len(body['jobs']) == 1

    def test_batch_unknown_preset_before_jobs(self, internal_client, internal_env):
        source_root, _uploads = internal_env
        a = source_root / 'a.mkv'
        a.write_bytes(b'aaa')
        response = internal_client.post(
            '/api/internal/encode',
            json={'paths': [str(a)], 'preset_id': 'missing'},
        )
        assert response.status_code == 400
        assert routes.jobs_storage == {}

    def test_fdk_preset_internal_encode_400(self, internal_client, internal_env, monkeypatch):
        monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
        source_root, _uploads = internal_env
        a = source_root / 'a.mkv'
        a.write_bytes(b'aaa')
        response = internal_client.post(
            '/api/internal/encode',
            json={'path': str(a), 'preset_id': 'aac_fdk_audio'},
        )
        assert response.status_code == 400
        assert 'libfdk_aac' in response.get_json()['error']
        assert routes.jobs_storage == {}

    def test_single_path_unchanged_shape(self, internal_client, internal_env):
        source_root, _uploads = internal_env
        a = source_root / 'solo.mkv'
        a.write_bytes(b'solo')
        response = internal_client.post(
            '/api/internal/encode',
            json={'path': str(a), 'preset_id': 'default'},
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['created'] is True
        assert 'job' in body
        assert 'jobs' not in body


class TestSystemInfoExtras:
    def test_system_info_ffmpeg_fields(self, client, monkeypatch):
        monkeypatch.setattr(routes, 'check_ffmpeg', lambda: True)
        monkeypatch.setattr(routes, 'get_ffmpeg_version_token', lambda: 'n9.0-test')
        monkeypatch.setattr(
            routes, 'get_ffmpeg_version_label', lambda: 'ffmpeg n9.0-test'
        )
        monkeypatch.setattr(
            routes,
            'get_ffmpeg_build_info',
            lambda: {
                'version_token': 'n9.0-test',
                'version_label': 'ffmpeg n9.0-test',
                'configuration': '--enable-gpl',
                'libraries': {'libavcodec': '61.0'},
            },
        )
        monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
        monkeypatch.setattr(
            routes, 'get_mkvmerge_status', lambda: ('ok', 'mkvmerge v90.0')
        )
        response = client.get('/api/system-info')
        assert response.status_code == 200
        data = response.get_json()
        assert data['ffmpeg_version'] == 'n9.0-test'
        assert data['ffmpeg_version_label'] == 'ffmpeg n9.0-test'
        assert data['ffmpeg_configuration'] == '--enable-gpl'
        assert data['ffmpeg_libraries']['libavcodec'] == '61.0'
        assert data['libfdk_aac'] is False
        assert data['mkvmerge_version'] == 'mkvmerge v90.0'


class TestPresetManager:
    def test_loaded_presets_match_plan(self):
        ids = set(preset_manager.presets.keys())
        expected = {
            'default', 'aac_audio', 'aac_fdk_audio', 'opus_audio', 'video_only',
            'bd_default', 'bd_opus_audio', 'bd_aac_audio', 'bd_aac_fdk_audio',
            'bd_video_only',
        }
        assert ids == expected
        assert preset_manager.get_preset('aac_audio').audio_settings.bitrate == '192k'
        assert preset_manager.get_preset('bd_default').preset == 'slower'
        assert preset_manager.get_preset('bd_default').crf == '21'
        assert preset_manager.get_preset('bd_default').group == 'remux'
