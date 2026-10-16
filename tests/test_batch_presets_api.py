"""Batch upload / internal encode и валидация video_id / audio_id / FDK."""

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
    def test_videos_audios_shape(self, client, monkeypatch):
        monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
        response = client.get('/api/presets')
        assert response.status_code == 200
        data = response.get_json()
        assert 'videos' in data and 'audios' in data
        assert 'presets' not in data and 'variations' not in data
        video_ids = {v['id'] for v in data['videos']}
        audio_ids = {a['id'] for a in data['audios']}
        assert video_ids == {'hevc_rip', 'hevc_remux', 'hevc_auto_level_rip'}
        assert audio_ids == {'copy', 'opus', 'aac', 'aac_fdk', 'none'}
        assert data['defaults']['video_id'] == 'hevc_auto_level_rip'
        assert data['defaults']['audio_id'] == 'copy'
        assert data['videos'][0]['id'] == 'hevc_auto_level_rip'
        assert data['videos'][0]['default'] is True
        for a in data['audios']:
            assert 'available' in a
            if a['id'] == 'aac_fdk':
                assert a['available'] is False
            else:
                assert a['available'] is True


class TestPresetValidation:
    def test_unknown_video_id_400(self, client):
        data = {
            'video_id': 'no_such_video',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        assert 'video' in response.get_json()['error'].lower()

    def test_unknown_audio_id_400(self, client):
        data = {
            'video_id': 'hevc_rip',
            'audio_id': 'flac',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        assert 'audio' in response.get_json()['error'].lower()

    def test_stereo_with_copy_400(self, client):
        data = {
            'video_id': 'hevc_rip',
            'audio_id': 'copy',
            'audio_downmix': 'stereo',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        err = response.get_json()['error'].lower()
        assert 'stereo' in err or 'copy' in err

    def test_stereo_unsupported_audio_map_400(self, client):
        data = {
            'video_id': 'hevc_rip',
            'audio_id': 'opus',
            'audio_downmix': 'stereo',
            'preset_modified': 'true',
            'video_map': '0:v:0',
            'audio_map': '0:a:eng',
            'subtitle_map': '0:s?',
            'data_map': '0:t?',
            'video_codec': 'libx265',
            'subtitle_codec': 'copy',
            'data_codec': 'copy',
            'preset': 'slow',
            'crf': '23',
            'pixel_format': 'yuv420p10le',
            'x265_params': 'level=5.1:ref=6',
            'tune': 'animation',
            'audio_codec': 'libopus',
            'audio_bitrate': '160k',
            'output_format': 'matroska',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        err = response.get_json()['error'].lower()
        assert 'audio_map' in err or 'неподдерживаемый' in err

    def test_fdk_preset_unavailable_400(self, client, monkeypatch):
        monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
        data = {
            'video_id': 'hevc_rip',
            'audio_id': 'aac_fdk',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 400
        assert 'libfdk_aac' in response.get_json()['error']

    def test_hevc_remux_opus_job_fields(self, client):
        data = {
            'video_id': 'hevc_remux',
            'audio_id': 'opus',
            'file': (io.BytesIO(b'x'), 'clip.mkv'),
        }
        response = client.post(
            '/api/upload',
            data=data,
            content_type='multipart/form-data',
        )
        assert response.status_code == 200
        job = response.get_json()['job']
        assert job['video_id'] == 'hevc_remux'
        assert job['audio_id'] == 'opus'
        assert 'preset_id' not in job
        assert 'variation' not in job
        assert job['ffmpeg_params']['crf'] == '21'
        assert job['ffmpeg_params']['preset'] == 'slower'
        assert job['preset_label'] == 'HEVC remux / Opus'


class TestBatchUpload:
    def test_batch_files_creates_jobs(self, client):
        data = {
            'video_id': 'hevc_rip',
            'audio_id': 'copy',
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
        assert body['jobs'][0]['video_id'] == 'hevc_auto_level_rip'
        assert body['jobs'][0]['audio_id'] == 'copy'

    def test_batch_partial_207(self, client):
        data = {
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
                'video_id': 'hevc_remux',
                'audio_id': 'opus',
                'audio_downmix': 'stereo',
            },
        )
        assert response.status_code == 200
        body = response.get_json()
        assert body['created'] == 2
        assert len(body['jobs']) == 2
        assert body['jobs'][0]['video_id'] == 'hevc_remux'
        assert body['jobs'][0]['audio_id'] == 'opus'
        assert body['jobs'][0]['audio_downmix'] == 'stereo'
        assert body['jobs'][0]['ffmpeg_params']['crf'] == '21'

    def test_single_element_paths_batch_shape(self, internal_client, internal_env):
        source_root, _uploads = internal_env
        a = source_root / 'only.mkv'
        a.write_bytes(b'only')
        response = internal_client.post(
            '/api/internal/encode',
            json={'paths': [str(a)]},
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
            json={'paths': [str(a)], 'video_id': 'missing'},
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
            json={'path': str(a), 'audio_id': 'aac_fdk'},
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
            json={'path': str(a)},
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
        assert data['libfdk_aac'] is False


class TestPresetManager:
    def test_loaded_presets_match_plan(self):
        assert preset_manager.get_audio('aac').bitrate == '192k'
        remux = preset_manager.resolve('hevc_remux', 'copy')
        assert remux['preset'] == 'slower'
        assert remux['crf'] == '21'
        rip = preset_manager.resolve('hevc_rip', 'copy')
        assert rip['preset'] == 'slow'
        assert rip['crf'] == '23'
