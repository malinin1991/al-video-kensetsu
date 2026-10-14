"""Квитанции, доступность результата и идемпотентность internal encode."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from unittest.mock import MagicMock

import pytest
from werkzeug.datastructures import MultiDict

from app.main import app, status_callback
from app.job_store import JobStore, get_job_store
from app.models.job import Job, JobStatus, utc_now
import app.routes as routes


@pytest.fixture
def api_env(tmp_path, monkeypatch):
    root = tmp_path / 'anilibria'
    uploads = tmp_path / 'uploads'
    root.mkdir()
    uploads.mkdir()
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', str(root))
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'app_settings.json'))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)
    worker = MagicMock()
    worker.stop_job.return_value = {'removed_from_queue': True, 'had_process': False, 'process_dead': True}
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: worker)
    routes.jobs_storage.clear()
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield root, uploads, worker, client
    routes.jobs_storage.clear()


def video(root, name='Серия [01] .mkv'):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b'original')
    return str(path)


@pytest.mark.parametrize('as_form', [False, True])
def test_single_echoes_exact_path_in_post_get_and_storage(api_env, as_form):
    root, uploads, _, client = api_env
    path = f'  "{video(root)}"  '
    payload = {'path': path}
    response = client.post('/api/internal/encode', **({'data': payload} if as_form else {'json': payload}))
    assert response.status_code == 200
    job_data = response.get_json()['job']
    assert job_data['input_path'] == path
    assert job_data['filename'] == 'Серия [01] .mkv'
    job = routes.get_job(job_data['id'])
    assert Path(job.original_path).parent == uploads
    assert job.original_path != path
    assert client.get(f'/api/jobs/{job.id}').get_json()['input_path'] == path
    restored = get_job_store().recover()[0]
    assert restored.input_path == path
    assert restored.original_path == job.original_path


@pytest.mark.parametrize('as_form', [False, True])
def test_partial_batch_maps_equal_names_by_exact_path(api_env, as_form):
    root, _, _, client = api_env
    first = video(root, 'первый/Серия [01].mkv')
    second = video(root, 'второй/Серия [01].mkv')
    missing = str(root / 'третий/Серия [01].mkv')
    paths = [second, missing, first]
    response = client.post('/api/internal/encode', **({'data': MultiDict([('paths', p) for p in paths])}
                                                               if as_form else {'json': {'paths': paths}}))
    assert response.status_code == 207
    body = response.get_json()
    successes = {j['input_path']: j['id'] for j in reversed(body['jobs'])}
    assert set(successes) == {first, second}
    assert len(set(successes.values())) == 2
    assert body['errors'][0]['path'] == missing
    for path, job_id in successes.items():
        assert client.get(f'/api/jobs/{job_id}').get_json()['input_path'] == path


def test_status_and_download_availability(api_env, tmp_path):
    root, _, _, client = api_env
    body = client.post('/api/internal/encode', json={'path': video(root)}).get_json()
    job = routes.get_job(body['job']['id'])
    url = f'/api/jobs/{job.id}'
    output = tmp_path / 'output.mkv'
    output.write_bytes(b'final-mkv')
    job.output_path = str(output)
    job.progress = 100.0
    job.completed_at = utc_now()
    for status in (JobStatus.PLANNED, JobStatus.IN_PROGRESS, JobStatus.ERROR, JobStatus.CANCELLED):
        status_callback(job.id, status, 'Ошибка' if status == JobStatus.ERROR else None)
        data = client.get(url).get_json()
        assert data['status'] == status.value
        assert data['output_available'] is False
        assert data['download_url'] is None
        assert data['output_size_bytes'] is None
        assert data['completed_at'].endswith('Z')
        assert client.head(url + '/download').status_code == 400
    status_callback(job.id, JobStatus.DONE, None)
    data = client.get(url).get_json()
    assert data['output_available'] is True
    assert data['download_url'] == url + '/download'
    assert data['output_size_bytes'] == len(b'final-mkv')
    response = client.head(data['download_url'])
    assert response.status_code == 200
    assert response.content_length == data['output_size_bytes']
    output.unlink()
    data = client.get(url).get_json()
    assert data['status'] == 'done'
    assert data['output_available'] is False
    assert data['download_url'] is None
    assert data['output_size_bytes'] is None
    assert client.head(url + '/download').status_code == 404


@pytest.mark.parametrize('kind', ['directory', 'unreadable', 'missing', 'no_path', 'empty'])
def test_output_must_be_accessible_regular_file(api_env, tmp_path, monkeypatch, kind):
    _, _, _, client = api_env
    output = tmp_path / 'out.mkv'
    if kind == 'directory':
        output.mkdir()
    elif kind in ('unreadable', 'empty'):
        output.write_bytes(b'')
    job = Job('old-job', 'old.mkv', '/not-used', status=JobStatus.DONE,
              output_path=None if kind == 'no_path' else str(output))
    if kind == 'unreadable':
        monkeypatch.setattr('app.models.job.os.access', lambda *_args: False)
    routes.jobs_storage[job.id] = job
    data = client.get('/api/jobs/old-job').get_json()
    assert data['input_path'] is None
    assert data['output_available'] is (kind == 'empty')
    assert data['output_size_bytes'] == (0 if kind == 'empty' else None)
    assert client.head('/api/jobs/old-job/download').status_code == (200 if kind == 'empty' else 404)


def test_replay_is_immutable_without_recopy_or_resolve(api_env, monkeypatch):
    root, uploads, worker, client = api_env
    path = video(root)
    payload = {'path': path, 'request_id': 'altt-123', 'update_metadata': True}
    first = client.post('/api/internal/encode', json=payload)
    assert first.status_code == 200
    body = first.get_json()
    assert body['request_id'] == 'altt-123'
    assert body['job']['request_id'] == 'altt-123'
    Path(path).unlink()
    monkeypatch.setattr(routes, '_ffmpeg_params_from_json', lambda *_: pytest.fail('Повторный resolve'))
    replay = client.post('/api/internal/encode', json=dict(reversed(list(payload.items()))))
    assert replay.status_code == first.status_code
    assert replay.get_json() == body
    assert client.get(body['receipt_url']).get_json() == body
    assert worker.add_job.call_count == 1
    assert len(list(uploads.iterdir())) == 1


@pytest.mark.parametrize('change', [
    {'path': '/other.mkv'}, {'audio_id': 'opus'}, {'update_metadata': False},
    {'paths': ['/other.mkv']}, {'cpu_threads': 2}, {'metadata_video_title': 'Другое'},
    {'video_id': 'invalid'},
])
def test_conflicting_request_returns_409_before_side_effects(api_env, change):
    root, uploads, worker, client = api_env
    payload = {'path': video(root), 'request_id': 'conflict'}
    assert client.post('/api/internal/encode', json=payload).status_code == 200
    response = client.post('/api/internal/encode', json={**payload, **change})
    assert response.status_code == 409
    assert worker.add_job.call_count == 1
    assert len(list(uploads.iterdir())) == 1


def test_partial_receipt_remains_207_when_failed_source_appears(api_env):
    root, _, worker, client = api_env
    first = video(root, 'first/same.mkv')
    missing = str(root / 'missing.mkv')
    payload = {'paths': [missing, first], 'request_id': 'batch'}
    response = client.post('/api/internal/encode', json=payload)
    assert response.status_code == 207
    video(root, 'missing.mkv')
    replay = client.post('/api/internal/encode', json=payload)
    receipt = client.get(response.get_json()['receipt_url'])
    assert replay.status_code == receipt.status_code == 207
    assert replay.get_json() == receipt.get_json() == response.get_json()
    assert worker.add_job.call_count == 1
    assert client.post('/api/internal/encode', json={**payload, 'paths': [first, missing]}).status_code == 409


def test_failure_receipt_is_durable_and_not_retried(api_env):
    root, uploads, worker, client = api_env
    payload = {'path': str(root / 'missing.mkv'), 'request_id': 'missing'}
    response = client.post('/api/internal/encode', json=payload)
    assert response.status_code == 404
    video(root, 'missing.mkv')
    assert client.post('/api/internal/encode', json=payload).get_json() == response.get_json()
    receipt = client.get(response.get_json()['receipt_url'])
    assert receipt.status_code == 404
    assert receipt.get_json() == response.get_json()
    assert list(uploads.iterdir()) == []
    worker.add_job.assert_not_called()


@pytest.mark.parametrize('request_id', ['', 'has space', 'slash/id', 'юникод', 123, 'a' * 129])
def test_invalid_request_id_is_rejected_without_copying(api_env, request_id):
    root, uploads, worker, client = api_env
    response = client.post('/api/internal/encode', json={'path': video(root), 'request_id': request_id})
    assert response.status_code == 400
    assert list(uploads.iterdir()) == []
    worker.add_job.assert_not_called()


def test_form_request_id_and_receipt_lookup(api_env):
    root, _, worker, client = api_env
    payload = {'path': video(root), 'request_id': 'form'}
    first = client.post('/api/internal/encode', data=payload)
    assert first.status_code == 200
    assert client.post('/api/internal/encode', data=payload).get_json() == first.get_json()
    assert client.get('/api/internal/encode/requests/unknown').status_code == 404
    duplicate = MultiDict([('path', payload['path']), ('request_id', 'x'), ('request_id', 'y')])
    assert client.post('/api/internal/encode', data=duplicate).status_code == 400
    worker.add_job.assert_called_once()


def test_concurrent_repeats_get_202_then_same_receipt(api_env, monkeypatch):
    root, uploads, worker, client = api_env
    payload = {'paths': [video(root, 'one/same.mkv'), video(root, 'two/same.mkv')], 'request_id': 'concurrent'}
    copying = threading.Event()
    release = threading.Event()
    original_copy = routes.copy_internal_source_to_uploads
    copies = []

    def slow_copy(path):
        copies.append(path)
        copying.set()
        assert release.wait(timeout=10)
        return original_copy(path)

    monkeypatch.setattr(routes, 'copy_internal_source_to_uploads', slow_copy)

    def post():
        with app.test_client() as other_client:
            return other_client.post('/api/internal/encode', json=payload)

    with ThreadPoolExecutor(max_workers=2) as pool:
        future = pool.submit(post)
        try:
            assert copying.wait(timeout=5)
            pending = client.post('/api/internal/encode', json=payload)
            assert pending.status_code == 202
            assert pending.get_json()['request_status'] == 'processing'
            assert pending.headers['Retry-After'] == '2'
            lookup = client.get(pending.get_json()['receipt_url'])
            assert lookup.status_code == 202
            assert client.post('/api/internal/encode', json={**payload, 'audio_id': 'opus'}).status_code == 409
        finally:
            release.set()
        response = future.result(timeout=10)
    assert response.status_code == 200
    assert client.post('/api/internal/encode', json=payload).get_json() == response.get_json()
    assert client.get(response.get_json()['receipt_url']).get_json() == response.get_json()
    assert len(copies) == len(list(uploads.iterdir())) == worker.add_job.call_count == 2


def test_sqlite_claim_is_shared_between_independent_connections(tmp_path):
    path = str(tmp_path / 'shared.sqlite3')
    stores = [JobStore(path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def claim(store):
        barrier.wait(timeout=5)
        return store.claim_request('same', 'fingerprint')[0]

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(claim, stores))
    assert results.count(True) == 1


def test_completed_receipt_and_job_survive_fresh_python_process(api_env, tmp_path):
    root, _, worker, client = api_env
    payload = {'path': video(root), 'request_id': 'restart'}
    original = client.post('/api/internal/encode', json=payload).get_json()
    job = routes.get_job(original['job']['id'])
    output = tmp_path / 'done.mkv'
    output.write_bytes(b'final-output')
    job.output_path = str(output)
    job.progress = 100.0
    job.completed_at = utc_now()
    status_callback(job.id, JobStatus.DONE, None)
    script = '''
import json, sys
from flask import Flask
import app.routes as routes
class NoWork:
    def add_job(self, job):
        raise AssertionError('Повторный запуск')
routes.set_ffmpeg_worker(NoWork())
routes.restore_jobs()
app = Flask(__name__)
app.register_blueprint(routes.main_bp)
payload = json.load(sys.stdin)
with app.test_client() as client:
    response = client.post('/api/internal/encode', json=payload)
    receipt = response.get_json()
    job = client.get('/api/jobs/' + receipt['job']['id']).get_json()
    head = client.head(job['download_url'])
print('REPLAY_RESULT=' + json.dumps([response.status_code, receipt, job, head.status_code]))
'''
    result = subprocess.run([sys.executable, '-c', script], input=json.dumps(payload), text=True,
                            capture_output=True, check=True, env={**os.environ, 'LOG_DIR': str(tmp_path / 'logs')})
    line = next(line for line in result.stdout.splitlines() if line.startswith('REPLAY_RESULT='))
    code, receipt, restored, head_code = json.loads(line.removeprefix('REPLAY_RESULT='))
    assert code == head_code == 200
    assert receipt == original
    assert restored['id'] == job.id
    assert restored['input_path'] == payload['path']
    assert restored['status'] == 'done'
    assert restored['output_size_bytes'] == output.stat().st_size
    worker.add_job.assert_called_once()


def test_interrupted_request_preserves_known_ids_and_never_resubmits(api_env, monkeypatch):
    root, _, worker, client = api_env
    payload = {'path': video(root), 'request_id': 'interrupted'}
    # Потеря процесса после сохранения задания, до фиксации окончательной квитанции.
    def crash(*_args):
        raise SystemExit('simulated crash')

    monkeypatch.setattr(get_job_store(), 'finish_request', crash)
    with pytest.raises(SystemExit):
        client.post('/api/internal/encode', json=payload)
    job_id = next(iter(routes.jobs_storage))
    routes.jobs_storage.clear()
    routes.restore_jobs()
    for response in (client.post('/api/internal/encode', json=payload),
                     client.get('/api/internal/encode/requests/interrupted')):
        assert response.status_code == 202
        assert response.get_json()['request_status'] == 'interrupted'
        assert response.get_json()['job_ids'] == [job_id]
        assert 'Retry-After' not in response.headers
    job = client.get(f'/api/jobs/{job_id}').get_json()
    assert job['status'] == 'error'
    assert 'перезапуском' in job['error_message']
    assert job['input_path'] == payload['path']
    worker.add_job.assert_called_once()


def test_deleted_jobs_are_not_restored_but_receipt_stays(api_env):
    root, _, worker, client = api_env
    payload = {'path': video(root), 'request_id': 'deleted'}
    original = client.post('/api/internal/encode', json=payload).get_json()
    job_id = original['job']['id']
    assert client.delete(f'/api/jobs/{job_id}/delete').status_code == 200
    routes.restore_jobs()
    assert client.get(f'/api/jobs/{job_id}').status_code == 404
    assert client.post('/api/internal/encode', json=payload).get_json() == original
    worker.add_job.assert_called_once()


def test_cancelled_job_remains_cancelled_after_restart(api_env):
    root, _, _, client = api_env
    body = client.post('/api/internal/encode', json={'path': video(root)}).get_json()
    job_id = body['job']['id']
    assert client.post(f'/api/jobs/{job_id}/stop').status_code == 200
    routes.jobs_storage.clear()
    routes.restore_jobs()
    data = client.get(f'/api/jobs/{job_id}').get_json()
    assert data['status'] == 'cancelled'
    assert data['completed_at'].endswith('Z')


def test_queue_failure_cleans_job_source_and_request_link(api_env):
    root, uploads, worker, client = api_env
    worker.add_job.side_effect = RuntimeError('queue unavailable')
    response = client.post('/api/internal/encode', json={'path': video(root), 'request_id': 'queue-failed'})
    assert response.status_code == 500
    assert routes.get_all_jobs() == get_job_store().recover() == []
    assert get_job_store().request_job_ids('queue-failed') == []
    assert list(uploads.iterdir()) == []


def test_old_record_and_terminal_statuses_restore(tmp_path):
    store = get_job_store()
    for status in JobStatus:
        job = Job(status.value, 'old.mkv', '/old', status=status,
                  error_message='original-error' if status == JobStatus.ERROR else None,
                  completed_at=utc_now() if status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED) else None)
        record = job.to_record()
        record.pop('input_path')
        record.pop('request_id')
        store.insert_job(Job.from_record(record))
    restored = {job.id: job for job in store.recover()}
    assert all(job.input_path is None for job in restored.values())
    assert all(job.completed_at for job in restored.values())
    for status in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED):
        assert restored[status.value].status == status
    assert restored['error'].error_message == 'original-error'
    assert restored['planned'].status == restored['in_progress'].status == JobStatus.ERROR


def test_disabled_internal_mode_blocks_receipt_and_post(api_env, monkeypatch):
    root, _, _, client = api_env
    payload = {'path': video(root), 'request_id': 'disabled'}
    assert client.post('/api/internal/encode', json=payload).status_code == 200
    monkeypatch.setenv('INTERNAL_SOURCE_ROOTS', '')
    assert client.post('/api/internal/encode', json=payload).status_code == 403
    assert client.get('/api/internal/encode/requests/disabled').status_code == 403
