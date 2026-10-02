"""Параллельное кодирование и batch download/delete."""

from __future__ import annotations

import hashlib
import io
import threading
import time
import zipfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from app.ffmpeg_worker import FFmpegWorker
from app.main import app
import app.routes as routes
from app.models.job import Job, JobStatus


@pytest.fixture
def client(tmp_path, monkeypatch):
    uploads = tmp_path / 'uploads'
    uploads.mkdir()
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'app_settings.json'))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)
    monkeypatch.delenv('INTERNAL_SOURCE_ROOTS', raising=False)

    dummy = MagicMock()
    dummy.stop_job.return_value = {
        'removed_from_queue': False,
        'had_process': False,
        'process_dead': True,
    }
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: dummy)
    routes.jobs_storage.clear()
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client
    routes.jobs_storage.clear()


def _make_done_job(tmp_path: Path, job_id: str, filename: str, content: bytes) -> Job:
    original = tmp_path / f'{job_id}_src.mkv'
    original.write_bytes(b'src')
    output = tmp_path / f'{job_id}_out.mkv'
    output.write_bytes(content)
    job = Job(id=job_id, filename=filename, original_path=str(original))
    job.status = JobStatus.DONE
    job.output_path = str(output)
    return job


class TestConcurrentEncodes:
    def test_default_max_concurrent_is_one(self, monkeypatch, tmp_path):
        monkeypatch.setenv('APP_SETTINGS_PATH', str(tmp_path / 'app_settings.json'))
        monkeypatch.delenv('MAX_CONCURRENT_ENCODES', raising=False)

        worker = FFmpegWorker.__new__(FFmpegWorker)
        worker.progress_callback = None
        worker.log_callback = None
        worker.status_callback = None
        worker.running_jobs = {}
        worker.job_queue = []
        worker.worker_thread = None
        worker.stop_event = threading.Event()
        worker.processes = {}
        worker._cancel_requested = set()
        worker._missing_total_frames_logged = set()
        worker._lock = threading.Lock()
        worker._job_threads = {}
        # Как в __init__: берём из settings_store
        from app.settings_store import get_max_concurrent_encodes
        worker.max_concurrent = get_max_concurrent_encodes()
        assert worker.max_concurrent == 1

    def test_set_max_concurrent_affects_next_slots(self, monkeypatch):
        started = []
        release = threading.Event()
        lock = threading.Lock()

        def slow_process(self, job):
            with lock:
                started.append(job.id)
                self.running_jobs[job.id] = job
            job.status = JobStatus.IN_PROGRESS
            release.wait(timeout=5)
            with self._lock:
                self.running_jobs.pop(job.id, None)

        worker = FFmpegWorker.__new__(FFmpegWorker)
        worker.progress_callback = None
        worker.log_callback = None
        worker.status_callback = None
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

        monkeypatch.setattr(FFmpegWorker, '_process_job', slow_process)
        worker.start_worker()

        job1 = Job(
            id='11111111-1111-1111-1111-111111111111',
            filename='a.mkv',
            original_path='/tmp/a.mkv',
        )
        job2 = Job(
            id='22222222-2222-2222-2222-222222222222',
            filename='b.mkv',
            original_path='/tmp/b.mkv',
        )
        worker.add_job(job1)
        worker.add_job(job2)

        deadline = time.time() + 2
        while time.time() < deadline:
            with lock:
                if len(started) >= 1:
                    break
            time.sleep(0.05)

        with lock:
            assert len(started) == 1

        # Повышаем лимит без рестарта — второй слот должен стартовать
        assert worker.set_max_concurrent(2) == 2

        deadline = time.time() + 2
        while time.time() < deadline:
            with lock:
                if len(started) >= 2:
                    break
            time.sleep(0.05)

        with lock:
            assert len(started) == 2

        release.set()
        worker.stop_event.set()
        if worker.worker_thread:
            worker.worker_thread.join(timeout=2)

    def test_two_jobs_start_in_parallel(self, monkeypatch):
        monkeypatch.setenv('MAX_CONCURRENT_ENCODES', '2')
        started = []
        release = threading.Event()
        lock = threading.Lock()

        def slow_process(self, job):
            with lock:
                started.append(job.id)
                self.running_jobs[job.id] = job
            job.status = JobStatus.IN_PROGRESS
            release.wait(timeout=5)
            with self._lock:
                self.running_jobs.pop(job.id, None)

        worker = FFmpegWorker.__new__(FFmpegWorker)
        worker.progress_callback = None
        worker.log_callback = None
        worker.status_callback = None
        worker.running_jobs = {}
        worker.job_queue = []
        worker.worker_thread = None
        worker.stop_event = threading.Event()
        worker.processes = {}
        worker._cancel_requested = set()
        worker._missing_total_frames_logged = set()
        worker._lock = threading.Lock()
        worker.max_concurrent = 2
        worker._job_threads = {}

        monkeypatch.setattr(FFmpegWorker, '_process_job', slow_process)
        worker.start_worker()

        job1 = Job(
            id='11111111-1111-1111-1111-111111111111',
            filename='a.mkv',
            original_path='/tmp/a.mkv',
        )
        job2 = Job(
            id='22222222-2222-2222-2222-222222222222',
            filename='b.mkv',
            original_path='/tmp/b.mkv',
        )
        worker.add_job(job1)
        worker.add_job(job2)

        deadline = time.time() + 3
        while time.time() < deadline:
            with lock:
                if len(started) >= 2:
                    break
            time.sleep(0.05)

        with worker._lock:
            running_count = len(worker.running_jobs)
            thread_count = len(
                [t for t in worker._job_threads.values() if t.is_alive()]
            )

        release.set()
        worker.stop_event.set()
        if worker.worker_thread:
            worker.worker_thread.join(timeout=2)

        assert len(started) == 2
        assert running_count == 2
        assert thread_count == 2


class TestBatchDownload:
    def test_zip_stored_two_done(self, client, tmp_path):
        job1 = _make_done_job(
            tmp_path,
            'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa',
            'ShowA.mkv',
            b'content-a',
        )
        job2 = _make_done_job(
            tmp_path,
            'bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb',
            'ShowB.mkv',
            b'content-b',
        )
        routes.jobs_storage[job1.id] = job1
        routes.jobs_storage[job2.id] = job2

        response = client.post(
            '/api/jobs/download',
            json={'job_ids': [job1.id, job2.id]},
        )
        assert response.status_code == 200
        assert response.mimetype == 'application/zip'

        expected_ids = sorted([job1.id, job2.id])
        hash8 = hashlib.blake2b(
            '\n'.join(expected_ids).encode('utf-8'), digest_size=4
        ).hexdigest()
        expected_name = f'kensetsu-{hash8}.zip'
        cd = response.headers.get('Content-Disposition', '')
        assert expected_name in cd

        data = response.get_data()
        with zipfile.ZipFile(io.BytesIO(data), 'r') as zf:
            assert all(
                info.compress_type == zipfile.ZIP_STORED for info in zf.infolist()
            )
            names = set(zf.namelist())
            assert job1.get_output_filename() in names
            assert job2.get_output_filename() in names
            comment = zf.comment.decode('utf-8')
            assert 'kensetsu batch; 2 files' in comment
            assert job1.id in comment and job2.id in comment

    def test_missing_on_disk_returns_409(self, client, tmp_path):
        job1 = _make_done_job(
            tmp_path,
            'cccccccc-cccc-cccc-cccc-cccccccccccc',
            'Ok.mkv',
            b'ok',
        )
        job2 = Job(
            id='dddddddd-dddd-dddd-dddd-dddddddddddd',
            filename='Missing.mkv',
            original_path=str(tmp_path / 'src.mkv'),
        )
        job2.status = JobStatus.DONE
        job2.output_path = str(tmp_path / 'does-not-exist.mkv')
        routes.jobs_storage[job1.id] = job1
        routes.jobs_storage[job2.id] = job2

        response = client.post(
            '/api/jobs/download',
            json={'job_ids': [job1.id, job2.id]},
        )
        assert response.status_code == 409
        body = response.get_json()
        assert 'missing' in body
        assert len(body['missing']) == 1
        assert body['missing'][0]['job_id'] == job2.id
        assert response.mimetype != 'application/zip' or not response.get_data().startswith(
            b'PK'
        )

    def test_skips_non_done_returns_400_if_none(self, client, tmp_path):
        job = Job(
            id='eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee',
            filename='Planned.mkv',
            original_path=str(tmp_path / 'p.mkv'),
        )
        job.status = JobStatus.PLANNED
        routes.jobs_storage[job.id] = job

        response = client.post(
            '/api/jobs/download',
            json={'job_ids': [job.id]},
        )
        assert response.status_code == 400
        assert 'Нет готовых' in response.get_json()['error']


class TestBatchDelete:
    def test_partial_delete_returns_207(self, client, tmp_path):
        job = _make_done_job(
            tmp_path,
            'ffffffff-ffff-ffff-ffff-ffffffffffff',
            'Del.mkv',
            b'del',
        )
        routes.jobs_storage[job.id] = job
        missing_id = '00000000-0000-0000-0000-000000000000'

        response = client.post(
            '/api/jobs/delete',
            json={'job_ids': [job.id, missing_id]},
        )
        assert response.status_code == 207
        body = response.get_json()
        assert job.id in body['deleted']
        assert any(err['job_id'] == missing_id for err in body['errors'])
        assert job.id not in routes.jobs_storage
        assert not Path(job.output_path).exists()

    def test_single_delete_uses_helper(self, client, tmp_path):
        job = _make_done_job(
            tmp_path,
            '99999999-9999-9999-9999-999999999999',
            'One.mkv',
            b'one',
        )
        routes.jobs_storage[job.id] = job
        response = client.delete(f'/api/jobs/{job.id}/delete')
        assert response.status_code == 200
        assert job.id not in routes.jobs_storage
