"""Внутренний режим: файл с примонтированного хранилища."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.main import app
from app.models.job import Job, JobStatus
import app.routes as routes
from app.internal_source import (
    InternalSourceError,
    bind_original_to_job,
    cleanup_job_original,
    is_internal_source_path,
    is_temp_upload_path,
    normalize_source_path,
    original_in_use_by_jobs,
    resolve_internal_source_path,
)


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
def client(internal_env):
    with app.test_client() as test_client:
        yield test_client


def make_video(root: Path, name: str = 'video.mkv') -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'fake-video-bytes')
    return path


class TestNormalizeSourcePath:
    def test_strips_spaces_and_double_quotes(self):
        assert normalize_source_path('  "/anilibria/a.mkv"  ') == '/anilibria/a.mkv'

    def test_strips_single_quotes(self):
        assert normalize_source_path("'/anilibria/b.mkv'") == '/anilibria/b.mkv'

    def test_strips_nested_quotes(self):
        assert normalize_source_path('""/anilibria/c.mkv""') == '/anilibria/c.mkv'

    def test_empty_none(self):
        assert normalize_source_path(None) == ''
        assert normalize_source_path('   ') == ''


class TestInternalEncodeApi:
    def test_quoted_path_succeeds(self, client, internal_env):
        source_root, uploads = internal_env
        video = make_video(source_root, 'Show [08].mkv')
        quoted = f'  "{video}"  '
        response = client.post('/api/internal/encode', json={'path': quoted})
        assert response.status_code == 200
        data = response.get_json()
        assert data['created'] is True
        assert data['queued'] is True
        assert data['encoding_started'] is False
        assert data['message'] == 'Задание создано и добавлено в очередь'
        assert data['job']['filename'] == 'Show [08].mkv'
        copied = list(uploads.iterdir())
        assert len(copied) == 1
        assert copied[0].name.endswith('_Show [08].mkv')
        assert copied[0].read_bytes() == b'fake-video-bytes'

    def test_same_file_twice_gets_unique_originals(self, client, internal_env):
        source_root, uploads = internal_env
        video = make_video(source_root, 'episode.mkv')

        first = client.post(
            '/api/internal/encode',
            json={'path': str(video), 'preset_id': 'aac_audio'},
        )
        second = client.post(
            '/api/internal/encode',
            json={'path': str(video), 'preset_id': 'opus_audio'},
        )
        assert first.status_code == 200
        assert second.status_code == 200

        job1 = routes.get_job(first.get_json()['job']['id'])
        job2 = routes.get_job(second.get_json()['job']['id'])
        assert job1 is not None and job2 is not None
        assert job1.original_path != job2.original_path
        assert job1.filename == 'episode.mkv'
        assert job2.filename == 'episode.mkv'
        assert os.path.basename(job1.original_path).startswith(f'{job1.id}_')
        assert os.path.basename(job2.original_path).startswith(f'{job2.id}_')
        assert os.path.exists(job1.original_path)
        assert os.path.exists(job2.original_path)
        assert video.exists()

        cleanup_job_original(job1.original_path)
        assert not os.path.exists(job1.original_path)
        assert os.path.exists(job2.original_path)
        assert video.exists()

    def test_path_traversal_forbidden(self, client, internal_env):
        source_root, _uploads = internal_env
        outside = source_root.parent / 'secret.mkv'
        outside.write_bytes(b'secret')
        evil = os.path.join(str(source_root), os.pardir, 'secret.mkv')
        response = client.post('/api/internal/encode', json={'path': evil})
        assert response.status_code == 403
        assert 'error' in response.get_json()

    def test_symlink_escape_forbidden(self, client, internal_env):
        source_root, _uploads = internal_env
        outside_dir = source_root.parent / 'outside'
        outside_dir.mkdir()
        outside = outside_dir / 'secret.mkv'
        outside.write_bytes(b'secret')
        link = source_root / 'escape.mkv'
        try:
            os.symlink(str(outside), str(link))
        except OSError:
            pytest.skip('Создание symlink недоступно')
        response = client.post('/api/internal/encode', json={'path': str(link)})
        assert response.status_code == 403

    def test_missing_file_not_found(self, client, internal_env):
        source_root, _uploads = internal_env
        missing = source_root / 'nope.mkv'
        response = client.post('/api/internal/encode', json={'path': str(missing)})
        assert response.status_code == 404
        assert response.get_json()['error'] == 'Файл не найден'

    def test_wrong_extension_rejected(self, client, internal_env):
        source_root, _uploads = internal_env
        txt = source_root / 'notes.txt'
        txt.write_text('not video', encoding='utf-8')
        response = client.post('/api/internal/encode', json={'path': str(txt)})
        assert response.status_code == 400
        assert response.get_json()['error'] == 'Неподдерживаемый тип файла'

    def test_unknown_preset_rejected(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        response = client.post(
            '/api/internal/encode',
            json={'path': str(video), 'preset': 'no_such_preset'},
        )
        assert response.status_code == 400
        assert response.get_json()['error'] == 'Неизвестный пресет: no_such_preset'

    def test_preset_by_id(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        response = client.post(
            '/api/internal/encode',
            json={'path': str(video), 'preset_id': 'aac_audio'},
        )
        assert response.status_code == 200
        job = response.get_json()['job']
        assert job['preset_id'] == 'aac_audio'
        assert job['ffmpeg_params']['audio_codec'] == 'aac'

    def test_preset_by_name_ambiguous_400(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        response = client.post(
            '/api/internal/encode',
            json={'path': str(video), 'preset': 'HEVC + AAC'},
        )
        assert response.status_code == 400
        err = response.get_json()['error'].lower()
        assert 'неоднознач' in err
        assert 'aac_audio' in err
        assert 'bd_aac_audio' in err

    def test_default_preset_when_omitted(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        response = client.post('/api/internal/encode', json={'path': str(video)})
        assert response.status_code == 200
        data = response.get_json()
        assert data['created'] is True
        assert data['job']['preset_id'] == 'default'
        assert data['job']['ffmpeg_params']['video_codec'] == 'libx265'

    def test_form_uses_ffmpeg_fields(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root, 'clip.mp4')
        response = client.post(
            '/api/internal/encode',
            data={
                'path': str(video),
                'preset_id': 'opus_audio',
                'metadata_nickname': 'Tester',
            },
        )
        assert response.status_code == 200
        job = response.get_json()['job']
        assert job['preset_id'] == 'opus_audio'
        assert job['filename'] == 'clip.mp4'
        assert job['ffmpeg_params']['metadata_nickname'] == 'Tester'

    def test_json_nickname_from_payload(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        response = client.post(
            '/api/internal/encode',
            json={'path': str(video), 'metadata_nickname': 'GeeKaZ0iD'},
        )
        assert response.status_code == 200
        assert response.get_json()['job']['ffmpeg_params']['metadata_nickname'] == 'GeeKaZ0iD'

    def test_json_video_title_from_payload_not_persisted(self, client, internal_env):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        response = client.post(
            '/api/internal/encode',
            json={
                'path': str(video),
                'metadata_nickname': 'GeeKaZ0iD',
                'metadata_video_title': 'Original',
            },
        )
        assert response.status_code == 200
        params = response.get_json()['job']['ffmpeg_params']
        assert params['metadata_video_title'] == 'Original'
        assert params['metadata_nickname'] == 'GeeKaZ0iD'
        # Поле только на job/пак — в settings его нет
        settings = client.get('/api/settings/metadata').get_json()
        assert 'metadata_video_title' not in settings

    def test_relative_path_rejected(self, client):
        response = client.post(
            '/api/internal/encode',
            json={'path': 'relative/video.mkv'},
        )
        assert response.status_code == 400
        assert 'абсолютн' in response.get_json()['error'].lower()

    def test_empty_roots_disables_mode(self, client, internal_env, monkeypatch):
        source_root, _uploads = internal_env
        video = make_video(source_root)
        monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', '')
        response = client.post('/api/internal/encode', json={'path': str(video)})
        assert response.status_code == 403
        assert 'отключ' in response.get_json()['error'].lower()

    def test_missing_path(self, client):
        response = client.post('/api/internal/encode', json={})
        assert response.status_code == 400


class TestDeleteJobSidecars:
    def test_delete_removes_partial_remux_tmp_and_json(self, client, tmp_path):
        from app.mkvmerge_remux import (
            encode_partial_path,
            option_file_path,
            remux_temp_path,
        )
        from app.models.job import Job

        original = tmp_path / 'in.mkv'
        original.write_bytes(b'orig')
        output = tmp_path / 'Show.mkv'
        output.write_bytes(b'final')

        job = Job(
            id='deadbeef-1234-5678-sidecars',
            filename='Show.mkv',
            original_path=str(original),
        )
        job.output_path = str(output)
        routes.jobs_storage[job.id] = job
        routes.get_ffmpeg_worker_instance().stop_job.return_value = {
            'removed_from_queue': False,
            'had_process': False,
            'process_dead': True,
        }

        partial = Path(encode_partial_path(str(output)))
        remux_tmp = Path(remux_temp_path(str(output)))
        json_file = Path(option_file_path(str(output), job.id))
        partial.write_bytes(b'partial')
        remux_tmp.write_bytes(b'tmp')
        json_file.write_text('[]', encoding='utf-8')

        response = client.delete(f'/api/jobs/{job.id}/delete')
        assert response.status_code == 200
        assert not original.exists()
        assert not output.exists()
        assert not partial.exists()
        assert not remux_tmp.exists()
        assert not json_file.exists()
        assert job.id not in routes.jobs_storage

    def test_delete_keeps_internal_original(self, client, internal_env, tmp_path):
        source_root, _uploads = internal_env
        original = make_video(source_root, 'library.mkv')
        output = tmp_path / 'Show.mkv'
        output.write_bytes(b'final')

        job = Job(
            id='keep-internal-original',
            filename='library.mkv',
            original_path=str(original),
        )
        job.output_path = str(output)
        routes.jobs_storage[job.id] = job
        routes.get_ffmpeg_worker_instance().stop_job.return_value = {
            'removed_from_queue': False,
            'had_process': False,
            'process_dead': True,
        }

        response = client.delete(f'/api/jobs/{job.id}/delete')
        assert response.status_code == 200
        assert original.exists()
        assert not output.exists()
        assert job.id not in routes.jobs_storage

    def test_delete_keeps_original_used_by_other_job(self, client, tmp_path):
        source = tmp_path / 'shared.mkv'
        source.write_bytes(b'shared-source')
        output1 = tmp_path / 'out1.mkv'
        output1.write_bytes(b'final1')

        job1 = Job(
            id='delete-shared-one',
            filename='shared.mkv',
            original_path=str(source),
        )
        job1.status = JobStatus.DONE
        job1.output_path = str(output1)
        job2 = Job(
            id='delete-shared-two',
            filename='shared.mkv',
            original_path=str(source),
        )
        job2.status = JobStatus.PLANNED
        routes.jobs_storage[job1.id] = job1
        routes.jobs_storage[job2.id] = job2
        routes.get_ffmpeg_worker_instance().stop_job.return_value = {
            'removed_from_queue': False,
            'had_process': False,
            'process_dead': True,
        }

        response = client.delete(f'/api/jobs/{job1.id}/delete')
        assert response.status_code == 200
        assert source.exists()
        assert not output1.exists()
        assert job1.id not in routes.jobs_storage
        assert job2.id in routes.jobs_storage


class TestOriginalPathKind:
    def test_upload_copy_is_temp_not_internal(self, internal_env):
        source_root, uploads = internal_env
        library = make_video(source_root, 'show.mkv')
        copy = uploads / 'uuid_show.mkv'
        copy.write_bytes(b'copy')
        assert is_internal_source_path(str(library))
        assert not is_temp_upload_path(str(library))
        assert is_temp_upload_path(str(copy))
        assert not is_internal_source_path(str(copy))


class TestBindOriginalToJob:
    def test_renames_temp_upload(self, internal_env):
        _source_root, uploads = internal_env
        source = uploads / 'uuid_clip.mkv'
        source.write_bytes(b'payload')
        bound = bind_original_to_job(str(source), 'job-aaa', 'clip.mkv')
        assert os.path.basename(bound) == 'job-aaa_clip.mkv'
        assert os.path.exists(bound)
        assert not source.exists()
        assert Path(bound).read_bytes() == b'payload'

    def test_copies_when_shared(self, internal_env):
        _source_root, uploads = internal_env
        source = uploads / 'uuid_clip.mkv'
        source.write_bytes(b'payload')
        bound = bind_original_to_job(
            str(source), 'job-bbb', 'clip.mkv', shared=True
        )
        assert os.path.basename(bound) == 'job-bbb_clip.mkv'
        assert os.path.exists(bound)
        assert source.exists()
        assert Path(bound).read_bytes() == b'payload'

    def test_copies_library_file_without_renaming_it(self, internal_env):
        source_root, uploads = internal_env
        library = make_video(source_root, 'keep.mkv')
        bound = bind_original_to_job(str(library), 'job-ccc', 'keep.mkv')
        assert library.exists()
        assert Path(bound).parent == uploads
        assert os.path.basename(bound) == 'job-ccc_keep.mkv'
        assert Path(bound).read_bytes() == b'fake-video-bytes'

    def test_original_in_use_detects_active_job(self, tmp_path):
        source = tmp_path / 'shared.mkv'
        source.write_bytes(b'x')
        active = Job(
            id='other',
            filename='shared.mkv',
            original_path=str(source),
        )
        active.status = JobStatus.PLANNED
        done = Job(
            id='done',
            filename='shared.mkv',
            original_path=str(source),
        )
        done.status = JobStatus.DONE
        assert original_in_use_by_jobs(str(source), [active], 'current')
        assert not original_in_use_by_jobs(str(source), [done], 'current')
        assert not original_in_use_by_jobs(str(source), [active], 'other')


class TestResolveInternalSourcePath:
    def test_directory_rejected(self, internal_env):
        source_root, _uploads = internal_env
        with pytest.raises(InternalSourceError) as exc:
            resolve_internal_source_path(str(source_root))
        assert exc.value.status_code == 400
