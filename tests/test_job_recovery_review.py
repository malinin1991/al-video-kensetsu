"""Регрессии найденных при независимом ревью потерь данных и гонок."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from app.ffmpeg_worker import FFmpegWorker
from app.job_store import JobStore, get_job_store
from app.models.job import Job, JobStatus


def test_durable_admission_survives_failed_ram_publication(tmp_path, monkeypatch):
    import app.routes as routes

    class UnavailableProjection(dict):
        def setdefault(self, key, value=None):
            raise MemoryError('RAM projection unavailable')

    uploads = tmp_path/'uploads'
    uploads.mkdir()
    source = uploads/'video.mkv'
    source.write_bytes(b'accepted source')
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setattr(routes, 'jobs_storage', UnavailableProjection())
    job, error = routes.create_job_from_saved_file(str(source), 'video.mkv', {})
    assert error is None and job is not None
    store = get_job_store()
    assert store.queued_ids() == [job.id]
    assert Path(store.load_job(job.id).original_path).read_bytes() == b'accepted source'

    # Новая RAM-проекция подбирает committed Job, даже если первоначальная не приняла его.
    (tmp_path/'outputs').mkdir()
    worker = FFmpegWorker(store=store, jobs={}, autostart=False)
    entered = threading.Event()
    monkeypatch.setattr(worker, '_process_job', lambda current: entered.set())
    worker.start_worker()
    try:
        assert entered.wait(timeout=5)
        assert worker.jobs[job.id].status == JobStatus.IN_PROGRESS
    finally:
        worker.stop_worker()


def test_route_keeps_job_already_loaded_and_claimed_by_dispatcher(tmp_path, monkeypatch):
    import app.main as main
    import app.routes as routes
    source = tmp_path/'video.mkv'
    source.write_bytes(b'source')
    store = get_job_store()
    jobs = {}
    monkeypatch.setattr(routes, 'jobs_storage', jobs)
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', MagicMock())
    insert = store.insert_job

    def insert_and_dispatch(job):
        insert(job)
        loaded = store.load_job(job.id)
        loaded.output_path = str(tmp_path/'encoded.mkv')
        jobs.setdefault(job.id, loaded)
        assert store.claim_job(loaded)

    monkeypatch.setattr(store, 'insert_job', insert_and_dispatch)
    submitted = Job('published', 'video.mkv', str(source))
    accepted = routes.add_job(submitted)
    assert accepted is jobs[submitted.id]
    assert accepted is not submitted
    assert accepted.status == JobStatus.IN_PROGRESS
    accepted.status = JobStatus.DONE
    main.status_callback(accepted.id, accepted.status, None)
    saved = store.load_job(accepted.id)
    assert saved.output_path == str(tmp_path/'encoded.mkv')
    assert saved.started_at is not None


def test_dispatcher_keeps_job_published_while_sqlite_load_is_in_flight(tmp_path, monkeypatch):
    store = get_job_store()
    original = Job('racing', 'video.mkv', '/source')
    store.insert_job(original)
    jobs = {}
    load = store.load_job

    def load_and_publish(job_id):
        loaded = load(job_id)
        jobs.setdefault(original.id, original)
        return loaded

    monkeypatch.setattr(store, 'load_job', load_and_publish)
    worker = FFmpegWorker(store=store, jobs=jobs, autostart=False)
    monkeypatch.setattr(worker, '_check_storage', lambda: None)
    entered = threading.Event()
    executed = []
    monkeypatch.setattr(worker, '_process_job', lambda job: (executed.append(job), entered.set()))
    worker.start_worker()
    try:
        assert entered.wait(timeout=5)
        assert executed == [original]
        assert jobs[original.id] is original
        assert original.status == JobStatus.IN_PROGRESS
    finally:
        worker.stop_worker()


@pytest.mark.parametrize('event', ['job_update', 'progress_update'])
def test_notification_failure_after_commit_does_not_pause_execution(tmp_path, monkeypatch, event):
    import app.main as main
    import app.routes as routes
    job = Job('notify', 'video.mkv', '/source')
    store = get_job_store()
    store.insert_job(job)
    store.claim_job(job)
    monkeypatch.setitem(routes.jobs_storage, job.id, job)
    worker = FFmpegWorker(status_callback=main.status_callback, store=store, autostart=False)
    monkeypatch.setattr(main.socketio, 'emit', MagicMock(side_effect=OSError('socket disconnected')))
    if event == 'job_update':
        job.status = JobStatus.DONE
        worker._publish_status(job)
        assert store.load_job(job.id).status == JobStatus.DONE
    else:
        main.progress_callback(job.id, 25)
        assert job.progress == 25
        assert store.load_job(job.id).status == JobStatus.IN_PROGRESS
    assert worker.storage_error is None


@pytest.mark.parametrize('pending', [JobStatus.ERROR, JobStatus.CANCELLED, JobStatus.DONE])
def test_other_cleanup_respects_uncommitted_terminal_source_reference(tmp_path, pending):
    from app.internal_source import original_in_use_by_jobs
    source = tmp_path/'source.mkv'
    source.write_bytes(b'preserved shared legacy source')
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    owner = Job('owner', 'video.mkv', str(source))
    other = Job('other', 'video.mkv', str(source))
    store.insert_job(other)
    store.claim_job(other)
    other.status = pending  # имитация failed commit терминального состояния
    worker = FFmpegWorker(jobs={other.id: other}, autostart=False)
    assert original_in_use_by_jobs(str(source), [other], owner.id)
    worker._cleanup_temp_original(owner)
    assert source.exists()


@pytest.mark.parametrize('field,value', [('audio_downmix', 'corrupt'), ('params', 'none')])
def test_corrupt_downmix_is_quarantined_without_changing_saved_parameters(tmp_path, field, value):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    job = Job('stereo', 'video.mkv', '/source', audio_id='opus', audio_downmix='stereo')
    store.insert_job(job)
    record = job.to_record()
    if field == 'params':
        record['ffmpeg_params']['audio_downmix'] = value
    else:
        record[field] = value
    raw = json.dumps(record)
    with sqlite3.connect(store.path) as db:
        db.execute('UPDATE jobs SET record=?', (raw,))
    assert store.recover()[0].status == JobStatus.ERROR
    assert store.queued_ids() == []
    with sqlite3.connect(store.path) as db:
        assert db.execute('SELECT record FROM jobs').fetchone()[0] == raw


def test_failed_route_write_also_pauses_dispatcher(tmp_path):
    store = get_job_store()
    job = Job('cancel', 'video.mkv', '/source')
    store.insert_job(job)
    worker = FFmpegWorker(store=store, autostart=False)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_state BEFORE UPDATE ON jobs BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    job.status = JobStatus.CANCELLED
    with pytest.raises(sqlite3.IntegrityError):
        store.save_job(job)
    assert worker.storage_error == 'disk failure'
    assert store.load_job(job.id).status == JobStatus.PLANNED


def test_stop_rechecks_helper_registered_while_joining_job_thread(monkeypatch):
    worker = FFmpegWorker(autostart=False)
    job = Job('late-helper', 'video.mkv', '/source', status=JobStatus.IN_PROGRESS)
    process = MagicMock()
    thread = MagicMock()
    thread.join.side_effect = lambda **_: worker.processes.setdefault(job.id, process)
    thread.is_alive.return_value = False
    worker._job_threads[job.id] = thread
    worker.running_jobs[job.id] = job
    monkeypatch.setattr(worker, '_kill_process', MagicMock(return_value=False))
    result = worker.stop_job(job.id)
    assert result['thread_stopped'] and result['had_process']
    assert not result['process_dead']
    assert job.status == JobStatus.IN_PROGRESS
    assert worker.processes[job.id] is process


def test_completion_pipe_is_closed_once_during_concurrent_checks(monkeypatch):
    import app.execution as execution
    reader, writer = os.pipe()
    os.close(writer)
    process = SimpleNamespace(_completion_fd=reader, _completion_lock=threading.Lock(),
                              _completion_finalizer=MagicMock())
    closing = threading.Event()
    release = threading.Event()
    actual_close = os.close
    closed = []

    def close(fd):
        if fd == reader:
            closing.set()
            assert release.wait(timeout=5)
            closed.append(fd)
        actual_close(fd)

    monkeypatch.setattr(execution.os, 'close', close)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(execution.media_process_finished, process)
        assert closing.wait(timeout=5)
        second = pool.submit(execution.media_process_finished, process)
        release.set()
        assert first.result(timeout=5) and second.result(timeout=5)
    assert closed == [reader]
    process._completion_finalizer.detach.assert_called_once()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX execution lease')
def test_database_symlink_cannot_bypass_execution_lease(tmp_path):
    target = tmp_path/'jobs.sqlite3'
    target.touch()
    alias = tmp_path/'alias.sqlite3'
    alias.symlink_to(target)
    script = '''
from app.execution import acquire_execution
import subprocess,sys
acquire_execution(sys.argv[1])
r=subprocess.run([sys.executable,'-c',
    'from app.execution import acquire_execution; import sys; acquire_execution(sys.argv[1], timeout=0)',
    sys.argv[2]], capture_output=True, text=True)
assert r.returncode != 0, 'alias bypassed lease'
assert 'База уже используется' in r.stderr
'''
    subprocess.run([sys.executable, '-c', script, str(target), str(alias)], check=True, timeout=10)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX guardian')
@pytest.mark.parametrize('guardian_signal', [signal.SIGSTOP, signal.SIGKILL])
def test_guardian_exit_is_not_proof_of_media_tree_exit(tmp_path, guardian_signal):
    # Отдельный процесс содержит свой lease; настоящий tool переживает SIGKILL wrapper.
    script = '''
import os, pathlib, signal, sys, time
import psutil
from app.execution import acquire_execution, popen_media, media_process_finished
from app.ffmpeg_worker import FFmpegWorker
root=pathlib.Path(sys.argv[1]); pidfile=root/'tool.pid'
acquire_execution(str(root/'jobs.sqlite3'))
code="import os,time,pathlib; pathlib.Path("+repr(str(pidfile))+").write_text(str(os.getpid())); time.sleep(60)"
process=popen_media([sys.executable,'-c',code])
worker=FFmpegWorker(autostart=False)
try:
    deadline=time.monotonic()+10
    while not pidfile.exists() and time.monotonic()<deadline: time.sleep(.01)
    assert pidfile.exists()
    child=psutil.Process(int(pidfile.read_text()))
    requested_signal=int(sys.argv[2]); os.kill(process.pid,requested_signal)
    if requested_signal==signal.SIGKILL: process.wait(timeout=5)
    confirmed=worker._kill_process(process,'job')
    alive=child.is_running() and child.status()!=psutil.STATUS_ZOMBIE
    if requested_signal==signal.SIGSTOP:
        assert confirmed and not alive
    else:
        assert not confirmed and alive
        assert worker.storage_error
finally:
    if pidfile.exists():
        try: os.killpg(int(pidfile.read_text()),signal.SIGKILL)
        except ProcessLookupError: pass
    if process.poll() is None:
        process.kill(); process.wait(timeout=5)
    deadline=time.monotonic()+5
    while not media_process_finished(process) and time.monotonic()<deadline: time.sleep(.02)
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path), str(int(guardian_signal))],
                   check=True, timeout=25)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX guardian')
@pytest.mark.parametrize('stage,action', [('preflight', 'stop'), ('finalize', 'delete')])
def test_orphan_synchronous_tool_blocks_stop_and_delete(tmp_path, stage, action):
    script = '''
import os, pathlib, signal, sys, threading, time
from flask import Flask
import psutil
import app.execution as execution
import app.ffmpeg_worker as worker_module
import app.routes as routes
from app.job_store import JobStore
from app.models.job import Job, JobStatus
root=pathlib.Path(sys.argv[1]); uploads=root/'uploads'; outputs=root/'outputs'
uploads.mkdir(); outputs.mkdir()
os.environ['UPLOADS_DIR']=str(uploads); os.environ['OUTPUTS_DIR']=str(outputs)
store=JobStore(str(root/'jobs.sqlite3')); execution.acquire_execution(store.path)
source=uploads/'source.mkv'; source.write_bytes(b'source must survive')
job=Job('orphan', 'source.mkv', str(source)); job.file_hash=job.calculate_hash()
store.insert_job(job); store.claim_job(job)
worker=worker_module.FFmpegWorker(store=store, jobs={job.id:job}, autostart=False,
    status_callback=lambda *_: store.save_job(job))
worker._ensure_mkvmerge_preflight=lambda _: None
# Убираем только задержку проверки EOF, сохраняя настоящий pipe и живые процессы.
finished=execution.media_process_finished
execution.media_process_finished=lambda process, timeout=0: finished(process)
worker_module.media_process_finished=execution.media_process_finished
pidfile=root/'helper.pid'; observed=[]
original_popen=execution.popen_media
def observe(*args, **kwargs):
    process=original_popen(*args, **kwargs); observed.append(process); return process
execution.popen_media=observe
def helper(*_):
    code='import os,pathlib,time; pathlib.Path('+repr(str(pidfile))+').write_text(str(os.getpid())); time.sleep(60)'
    execution.run_media([sys.executable, '-c', code], capture_output=True, timeout=.5)
if sys.argv[2]=='preflight':
    worker._warn_multiple_audio_tracks=helper
else:
    worker._warn_multiple_audio_tracks=lambda _: None
    worker._build_ffmpeg_command=lambda _, dest: [sys.executable,'-c',
        'import pathlib; pathlib.Path('+repr(dest)+').write_bytes(b"encoded")']
    worker._finalize_encoded_output=helper
routes.jobs_storage={job.id:job}; routes.get_job_store=lambda:store
routes.get_ffmpeg_worker_instance=lambda:worker; routes._emit_job_update=lambda _:None
app=Flask(__name__); app.register_blueprint(routes.main_bp)
thread=threading.Thread(target=worker._process_job,args=(job,)); worker._job_threads[job.id]=thread
thread.start()
try:
    deadline=time.monotonic()+5
    while not pidfile.exists() and time.monotonic()<deadline: time.sleep(.01)
    assert pidfile.exists()
    child=psutil.Process(int(pidfile.read_text())); process=observed[-1]
    process.kill(); process.wait(timeout=5)
    thread.join(timeout=5); assert not thread.is_alive()
    assert worker.processes[job.id] is process
    assert worker.storage_error
    method='post' if sys.argv[3]=='stop' else 'delete'
    response=getattr(app.test_client(),method)('/api/jobs/'+job.id+'/'+sys.argv[3])
    assert response.status_code==409, response.get_json()
    assert store.load_job(job.id).status==JobStatus.IN_PROGRESS
    assert source.exists()
    assert child.is_running() and child.status()!=psutil.STATUS_ZOMBIE
finally:
    if pidfile.exists():
        try: os.killpg(int(pidfile.read_text()),signal.SIGKILL)
        except ProcessLookupError: pass
    thread.join(timeout=5)
    for process in observed:
        if process.poll() is None: process.kill(); process.wait(timeout=5)
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path), stage, action],
                   check=True, timeout=20)
