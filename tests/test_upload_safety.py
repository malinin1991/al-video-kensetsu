"""Ошибки входных данных, догрузка чанков и гонки остановки воркера."""

from __future__ import annotations

import io
import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

from flask import Flask
import pytest
from werkzeug.exceptions import InternalServerError

import app.routes as routes
from app.ffmpeg_worker import FFmpegWorker
from app.models.job import Job, JobStatus


@pytest.fixture
def client(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('OUTPUTS_DIR', str(tmp_path / 'outputs'))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'settings.json'))
    dummy = MagicMock()
    dummy.stop_job.return_value = {'process_dead': True, 'thread_stopped': True}
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: dummy)
    monkeypatch.setattr(routes, '_emit_job_update', lambda job: None)
    monkeypatch.setattr(routes, 'has_libfdk_aac', lambda: False)
    routes.jobs_storage.clear()
    app = Flask(__name__)
    app.config.update(TESTING=True, MAX_CONTENT_LENGTH=16 * 1024 * 1024)
    app.register_blueprint(routes.main_bp)
    with app.test_client() as test_client:
        yield test_client
    routes.jobs_storage.clear()


def _chunk(client, number=0, total=2, file_id='upload-1', content=b'abc', **fields):
    data = {
        'file': (io.BytesIO(content), 'chunk'),
        'filename': 'video.mkv',
        'file_id': file_id,
        'chunk_number': str(number),
        'total_chunks': str(total),
        'update_metadata': 'false',
    }
    data.update(fields)
    return client.post('/api/upload', data=data, content_type='multipart/form-data')


@pytest.mark.parametrize(('file_id', 'number', 'total'), [
    ('x/../../escaped', 0, 2), ('../escaped', 0, 2), ('a' * 129, 0, 2),
    ('upload', 'oops', 2), ('upload', -1, 2), ('upload', 2, 2),
    ('upload', 0, 0), ('upload', 0, -2), ('upload', 0, 100001),
])
def test_invalid_chunk_metadata_never_writes_files(client, tmp_path, file_id, number, total):
    response = _chunk(client, number, total, file_id)
    assert response.status_code == 400
    assert list((tmp_path / 'uploads').iterdir()) == []
    assert not (tmp_path / 'escaped').exists()


def test_incomplete_chunk_fields_are_not_treated_as_full_upload(client, tmp_path):
    response = client.post('/api/upload', data={
        'file': (io.BytesIO(b'video'), 'video.mkv'), 'file_id': 'unfinished',
    })
    assert response.status_code == 400
    assert list((tmp_path / 'uploads').iterdir()) == []


def test_missing_chunk_preserves_parts_and_can_be_retried(client, tmp_path):
    assert _chunk(client, 0, 3, content=b'first').status_code == 200
    response = _chunk(client, 2, 3, content=b'last')
    assert response.status_code == 400
    uploads = tmp_path / 'uploads'
    session = uploads / 'temp_upload-1'
    assert (session / 'chunk_0000').read_bytes() == b'first'
    assert (session / 'chunk_0002').read_bytes() == b'last'
    assert list(uploads.iterdir()) == [session]
    assert _chunk(client, 1, 3, content=b'middle').status_code == 200
    completed = _chunk(client, 2, 3, content=b'last')
    assert completed.status_code == 200
    job = routes.get_job(completed.get_json()['job']['id'])
    assert Path(job.original_path).read_bytes() == b'firstmiddlelast'
    assert not session.exists()
    assert len(routes.jobs_storage) == 1


def test_failed_assembly_removes_partial_and_preserves_chunks(client, tmp_path, monkeypatch):
    assert _chunk(client, 0).status_code == 200
    original_copy = routes.shutil.copyfileobj

    def fail_copy(source, target, length=0):
        if str(getattr(target, 'name', '')).endswith('_video.mkv'):
            target.write(b'partial')
            raise OSError('disk full')
        return original_copy(source, target, length)

    monkeypatch.setattr(routes.shutil, 'copyfileobj', fail_copy)
    assert _chunk(client, 1).status_code == 500
    uploads = tmp_path / 'uploads'
    session = uploads / 'temp_upload-1'
    assert (session / 'chunk_0000').exists()
    assert (session / 'chunk_0001').exists()
    assert list(uploads.iterdir()) == [session]
    assert not routes.jobs_storage


@pytest.mark.parametrize('failure', [OSError('cleanup denied'), InternalServerError('unexpected cleanup error')])
def test_accepted_chunk_job_keeps_its_source_when_cleanup_fails(client, tmp_path, monkeypatch, failure):
    cleanup = MagicMock(side_effect=failure)
    monkeypatch.setattr(routes.shutil, 'rmtree', cleanup)
    response = _chunk(client, 0, 1, content=b'accepted-video')
    assert response.status_code == 200
    assert response.get_json()['completed'] is True
    job = routes.get_job(response.get_json()['job']['id'])
    assert Path(job.original_path).read_bytes() == b'accepted-video'
    assert (tmp_path / 'uploads' / 'temp_upload-1' / 'chunk_0000').exists()
    assert len(routes.jobs_storage) == 1
    cleanup.assert_called_once()


def test_chunk_session_metadata_cannot_change(client, tmp_path):
    assert _chunk(client, 0).status_code == 200
    assert _chunk(client, 1, filename='other.mkv').status_code == 409
    assert _chunk(client, 1, total=3).status_code == 409
    assert not (tmp_path / 'uploads' / 'temp_upload-1' / 'chunk_0001').exists()


@pytest.mark.parametrize('filename', ['/tmp/outside.mkv', '../../outside.mkv', '..\\outside.mkv'])
def test_chunk_filename_cannot_escape_output_directory(client, filename):
    response = _chunk(client, 0, 1, filename=filename)
    assert response.status_code == 200
    job = routes.get_job(response.get_json()['job']['id'])
    assert '/' not in job.filename and '\\' not in job.filename
    assert not job.filename.startswith('.')
    assert Path(job.get_output_filename()).name == job.get_output_filename()


def test_chunk_sum_enforces_file_limit_and_preserves_previous_chunk(client, tmp_path):
    # Каждый HTTP request с multipart меньше лимита, итоговый файл — больше.
    client.application.config['MAX_CONTENT_LENGTH'] = 4096
    assert _chunk(client, 0, content=b'a' * 2200).status_code == 200
    response = _chunk(client, 1, content=b'b' * 2200)
    assert response.status_code == 413
    session = tmp_path / 'uploads' / 'temp_upload-1'
    assert (session / 'chunk_0000').stat().st_size == 2200
    assert not (session / 'chunk_0001').exists()
    assert sorted(p.name for p in session.iterdir()) == ['chunk_0000', 'upload.json']
    assert not routes.jobs_storage


def test_oversized_http_request_returns_json_413(client):
    client.application.config['MAX_CONTENT_LENGTH'] = 32
    response = client.post('/api/upload', data={
        'file': (io.BytesIO(b'x' * 100), 'video.mkv'),
    })
    assert response.status_code == 413
    assert response.is_json


@pytest.mark.parametrize('field,value', [
    ('cpu_threads', 'no'), ('cpu_threads', '-1'), ('cpu_threads', 65),
    ('thread_queue_size', 8193), ('x265_pools', 33), ('x265_pools', 1.5),
    ('cpu_threads', True), ('cpu_threads', None),
])
def test_invalid_resource_params_rejected_before_source_copy(client, monkeypatch, field, value):
    copy_source = MagicMock()
    monkeypatch.setattr(routes, 'copy_internal_source_to_uploads', copy_source)
    response = client.post('/api/internal/encode', json={
        'path': '/anilibria/video.mkv', field: value,
    })
    assert response.status_code == 400
    assert field in response.get_json()['error']
    copy_source.assert_not_called()
    assert not routes.jobs_storage


def test_invalid_resource_form_rejected_before_file_save(client, tmp_path):
    response = client.post('/api/upload', data={
        'file': (io.BytesIO(b'video'), 'video.mkv'), 'cpu_threads': '1.5',
    })
    assert response.status_code == 400
    assert not list((tmp_path / 'uploads').iterdir())


@pytest.mark.parametrize('endpoint', ['/api/settings', '/api/settings/metadata'])
@pytest.mark.parametrize('payload', [None, [], [1], 'text', 42, True])
def test_settings_require_json_object(client, endpoint, payload):
    response = client.put(endpoint, data=json.dumps(payload), content_type='application/json')
    assert response.status_code == 400
    assert response.is_json


def test_system_info_keeps_component_data_when_boot_time_denied(client, monkeypatch):
    import psutil

    monkeypatch.setattr(psutil, 'boot_time', MagicMock(side_effect=PermissionError('sysctl denied')))
    monkeypatch.setattr(routes, 'check_ffmpeg', lambda: False)
    monkeypatch.setattr(routes, 'get_mkvmerge_status', lambda: ('error', None))
    response = client.get('/api/system-info')
    assert response.status_code == 200
    body = response.get_json()
    assert body['system_status'] == 'warning'
    assert body['uptime'] == 'Недоступно'
    assert body['ffmpeg_status'] == 'error'
    assert body['memory']['total'] > 0


def _worker():
    worker = FFmpegWorker.__new__(FFmpegWorker)
    worker.progress_callback = worker.log_callback = worker.status_callback = None
    worker.running_jobs = {}
    worker.job_queue = []
    worker.worker_thread = None
    worker.stop_event = threading.Event()
    worker.processes = {}
    worker._cancel_requested = set()
    worker._missing_total_frames_logged = set()
    worker._lock = threading.Lock()
    worker.max_concurrent = 1
    worker._job_threads = {}
    return worker


def test_add_job_does_not_reset_status_after_dispatcher_takes_it():
    worker = _worker()
    job = Job(id='status-race', filename='video.mkv', original_path='/tmp/input.mkv')

    class DispatchOnUnlock:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            # Детерминированный scheduler: диспетчер получает lock сразу после release.
            worker.job_queue.pop().status = JobStatus.IN_PROGRESS

    worker._lock = DispatchOnUnlock()
    worker.add_job(job)
    assert job.status == JobStatus.IN_PROGRESS


def test_worker_creates_configured_output_directory(tmp_path, monkeypatch):
    worker = _worker()
    output_dir = tmp_path / 'nested' / 'outputs'
    monkeypatch.setenv('OUTPUTS_DIR', str(output_dir))
    job = Job(id='local-output', filename='video.mkv', original_path='/tmp/input.mkv', ffmpeg_params={})
    monkeypatch.setattr(worker, '_ensure_mkvmerge_preflight', lambda job: None)
    monkeypatch.setattr(worker, '_warn_multiple_audio_tracks', lambda job: None)
    monkeypatch.setattr(worker, '_build_ffmpeg_command', lambda *args: ['ffmpeg'])
    monkeypatch.setattr(worker, '_run_ffmpeg', lambda *args: None)
    worker._process_job(job)
    assert output_dir.is_dir()
    assert job.output_path == str(output_dir / job.get_output_filename())


def test_read_failure_terminates_encoder_before_file_cleanup(monkeypatch):
    worker = _worker()
    job = Job(id='read-failure', filename='video.mkv', original_path='/tmp/input.mkv')
    process = MagicMock()
    process.stdout.readline.side_effect = OSError('pipe failure')
    monkeypatch.setattr('app.ffmpeg_worker.subprocess.Popen', lambda *args, **kwargs: process)
    kill = MagicMock(return_value=True)
    monkeypatch.setattr(worker, '_kill_process', kill)
    worker._run_ffmpeg(job, ['ffmpeg'])
    assert job.status == JobStatus.ERROR
    kill.assert_called_once_with(process, job.id)


def test_delete_waits_for_preflight_thread_without_process(client, tmp_path, monkeypatch):
    worker = _worker()
    original = tmp_path / 'uploads' / 'input.mkv'
    original.write_bytes(b'source')
    job = Job(id='delete-preflight', filename='video.mkv', original_path=str(original), ffmpeg_params={})
    routes.add_job(job)
    worker.job_queue.clear()
    entered = threading.Event()
    release = threading.Event()

    def preflight(_job):
        entered.set()
        assert release.wait(3)

    monkeypatch.setattr(worker, '_ensure_mkvmerge_preflight', preflight)
    monkeypatch.setattr(worker, '_cleanup_unsuccessful_job', lambda job: None)
    monkeypatch.setattr(worker, '_run_ffmpeg', lambda *args: pytest.fail('FFmpeg after delete'))
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: worker)
    job_thread = threading.Thread(target=worker._process_job, args=(job,))
    worker._job_threads[job.id] = job_thread
    job_thread.start()
    assert entered.wait(2)
    result = {}
    deletion = threading.Thread(target=lambda: result.update(routes.delete_job_by_id(job.id)))
    deletion.start()
    # Дожидаемся запроса отмены, сохраняя preflight заблокированным.
    for _ in range(100):
        if worker._is_cancel_requested(job.id):
            break
        threading.Event().wait(0.01)
    assert worker._is_cancel_requested(job.id)
    assert deletion.is_alive()
    assert original.exists()
    release.set()
    deletion.join(3)
    job_thread.join(3)
    assert not deletion.is_alive()
    assert result['stop']['thread_stopped'] is True
    assert job.id not in routes.jobs_storage
    assert not original.exists()
    assert not Path(job.output_path).exists()


def test_delete_refuses_cleanup_when_worker_has_not_stopped(client, tmp_path):
    original = tmp_path / 'uploads' / 'input.mkv'
    original.write_bytes(b'source')
    job = Job(id='still-running', filename='video.mkv', original_path=str(original))
    routes.add_job(job)
    routes.get_ffmpeg_worker_instance().stop_job.return_value = {
        'process_dead': True, 'thread_stopped': False,
    }
    response = client.delete(f'/api/jobs/{job.id}/delete')
    assert response.status_code == 409
    assert original.read_bytes() == b'source'
    assert routes.get_job(job.id) is job


def test_delete_waits_for_finalization_and_removes_its_late_output(client, tmp_path, monkeypatch):
    worker = _worker()
    original = tmp_path / 'uploads' / 'input.mkv'
    original.write_bytes(b'source')
    job = Job(id='delete-finalize', filename='video.mkv', original_path=str(original), ffmpeg_params={})
    routes.add_job(job)
    entered = threading.Event()
    release = threading.Event()

    def finalize(_job, _path):
        entered.set()
        assert release.wait(3)
        Path(_job.output_path).write_bytes(b'final')

    process = MagicMock()
    process.stdout.readline.return_value = ''
    process.wait.return_value = process.poll.return_value = 0
    monkeypatch.setattr('app.ffmpeg_worker.subprocess.Popen', lambda *args, **kwargs: process)
    monkeypatch.setattr(worker, '_ensure_mkvmerge_preflight', lambda job: None)
    monkeypatch.setattr(worker, '_build_ffmpeg_command', lambda *args: ['ffmpeg'])
    monkeypatch.setattr(worker, '_finalize_encoded_output', finalize)
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: worker)
    job_thread = threading.Thread(target=worker._process_job, args=(job,))
    worker._job_threads[job.id] = job_thread
    job_thread.start()
    assert entered.wait(2)
    result = {}
    deletion = threading.Thread(target=lambda: result.update(routes.delete_job_by_id(job.id)))
    deletion.start()
    for _ in range(100):
        if worker._is_cancel_requested(job.id):
            break
        threading.Event().wait(0.01)
    assert worker._is_cancel_requested(job.id)
    assert deletion.is_alive()
    release.set()
    deletion.join(3)
    job_thread.join(3)
    assert result['stop']['thread_stopped'] is True
    assert job.id not in routes.jobs_storage
    assert not original.exists()
    assert not Path(job.output_path).exists()


def test_stop_does_not_relabel_successful_finalization_as_cancelled(client, tmp_path):
    job = Job(id='stop-finalize', filename='video.mkv', original_path=str(tmp_path / 'input.mkv'))
    routes.add_job(job)
    job.status = JobStatus.IN_PROGRESS

    def finish_during_stop(_job_id):
        job.status = JobStatus.DONE
        return {'process_dead': True, 'thread_stopped': True}

    routes.get_ffmpeg_worker_instance().stop_job.side_effect = finish_during_stop
    response = client.post(f'/api/jobs/{job.id}/stop')
    assert response.status_code == 200
    assert job.status == JobStatus.DONE
