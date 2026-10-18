"""Постоянная очередь и аварии проверяются также в новых Python-процессах."""

import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from app.job_store import JobStore
from app.models.job import Job, JobStatus


def make_job(store, name):
    job = Job(name, name+'.mkv', '/source/'+name, file_hash='a'*64)
    store.insert_job(job)
    return job


def fresh_recover(path):
    script = '''
import json, sys
from app.job_store import JobStore
s = JobStore(sys.argv[1])
jobs = s.recover()
print(json.dumps({'jobs': {j.id:j.to_record() for j in jobs}, 'queue':s.queued_ids()}))
'''
    return json.loads(subprocess.check_output([sys.executable, '-c', script, str(path)], text=True))


def test_restart_prioritizes_active_and_preserves_attempts_and_parameters(tmp_path):
    path = tmp_path/'jobs.sqlite3'
    store = JobStore(str(path))
    a, b, c, d = [make_job(store, name) for name in 'ABCD']
    assert store.claim_job(a)
    assert store.claim_job(b)
    a.progress = 97
    store.save_job(a)
    restored = fresh_recover(path)
    assert restored['queue'] == list('ABCD')
    for name in 'AB':
        record = restored['jobs'][name]
        assert record['status'] == 'planned'
        assert record['attempt_no'] == 2
        assert record['progress'] == 0
        assert record['started_at'] is record['completed_at'] is None
        assert record['ffmpeg_params'] == a.to_record()['ffmpeg_params']
        assert record['preset_snapshot'] == a.to_record()['preset_snapshot']
    assert restored['jobs']['C']['attempt_no'] == restored['jobs']['D']['attempt_no'] == 1
    assert fresh_recover(path) == restored  # commit recovery уже состоялся
    with sqlite3.connect(path) as db:
        history = json.loads(db.execute('SELECT record FROM job_attempts WHERE job_id=? AND attempt_no=1', ('A',)).fetchone()[0])
        assert history['outcome'] == 'interrupted'
        assert history['progress'] == 97


@pytest.mark.parametrize('status', [JobStatus.ERROR, JobStatus.CANCELLED, JobStatus.DONE])
def test_terminal_jobs_never_restart_even_legacy_restart_error(tmp_path, status):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    job = make_job(store, 'terminal')
    job.status = status
    job.error_message = 'Задание прервано перезапуском сервера; автоматический повтор отключён'
    store.save_job(job)
    for _ in range(2):
        restored = fresh_recover(store.path)
        assert restored['jobs'][job.id]['status'] == status.value
        assert restored['jobs'][job.id]['attempt_no'] == 1
        assert restored['queue'] == []


@pytest.mark.parametrize('active', [False, True])
@pytest.mark.parametrize('action', ['stop', 'delete'])
def test_pending_actions_excluded_from_claim_and_recovery(tmp_path, active, action):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    job = make_job(store, 'action')
    if active:
        store.claim_job(job)
    store.set_action(job.id, action)
    assert store.queued_ids() == []
    assert not store.claim_job(job)
    restored = fresh_recover(store.path)
    assert restored['jobs'][job.id]['status'] == 'cancelled'
    assert restored['jobs'][job.id]['attempt_no'] == 1
    assert restored['queue'] == []
    assert store.actions() == {job.id: action}


def test_old_callback_cannot_change_new_attempt(tmp_path):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    old = make_job(store, 'A')
    store.claim_job(old)
    restored = store.recover()[0]
    old.status = JobStatus.ERROR
    assert store.save_job(old) is False
    assert fresh_recover(store.path)['jobs']['A']['attempt_no'] == restored.attempt_no == 2
    assert store.queued_ids() == ['A']


def test_done_is_not_public_before_commit(tmp_path):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    job = make_job(store, 'A')
    store.claim_job(job)
    output = tmp_path/'output.mkv'
    output.write_bytes(b'final')
    job.output_path = str(output)
    job.status = JobStatus.DONE
    assert job.to_dict()['status'] == 'in_progress'
    assert job.available_output_size() is None
    store.save_job(job)
    assert job.to_dict()['status'] == 'done'
    assert job.available_output_size() == 5


def test_missing_storage_root_pauses_without_failing_or_cleaning_queue(tmp_path, monkeypatch):
    from app.ffmpeg_worker import FFmpegWorker
    uploads = tmp_path/'uploads'
    outputs = tmp_path/'outputs'
    uploads.mkdir()
    outputs.mkdir()
    monkeypatch.setenv('UPLOADS_DIR', str(uploads))
    monkeypatch.setenv('OUTPUTS_DIR', str(outputs))
    source = uploads/'source.mkv'
    source.write_bytes(b'preserved')
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    job = Job('A', 'source.mkv', str(source))
    store.insert_job(job)
    worker = FFmpegWorker(store=store, jobs={job.id: job}, autostart=False)
    worker._check_storage()
    uploads.rename(tmp_path/'disconnected')
    worker.start_worker()
    try:
        deadline = time.monotonic()+3
        while worker.storage_error is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert worker.storage_error
        assert store.queued_ids() == ['A']
        assert fresh_recover(store.path)['jobs']['A']['status'] == 'planned'
        assert (tmp_path/'disconnected'/'source.mkv').read_bytes() == b'preserved'
    finally:
        worker.stop_worker()


@pytest.mark.parametrize('field,value', [('attempt_no', 'bad'), ('revision', None),
                                        ('recovery_count', False), ('dispatch_order', '1')])
def test_corrupt_execution_field_does_not_block_other_jobs(tmp_path, field, value):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    a = make_job(store, 'A')
    make_job(store, 'B')
    store.claim_job(a)
    with sqlite3.connect(store.path) as db:
        record = a.to_record()
        record[field] = value
        db.execute('UPDATE jobs SET record=? WHERE id=?', (json.dumps(record), 'A'))
    restored = fresh_recover(store.path)
    assert restored['jobs']['A']['status'] == 'error'
    assert restored['queue'] == ['B']


def test_finishing_stop_does_not_erase_concurrent_delete(tmp_path):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    make_job(store, 'A')
    store.set_action('A', 'stop')
    store.set_action('A', 'delete')
    store.finish_action('A')
    assert store.actions() == {'A': 'delete'}


def test_wrong_json_id_does_not_overwrite_another_job(tmp_path):
    path = tmp_path/'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        for sql_id, original in [('A', '/source/A'), ('B', '/source/B')]:
            job = Job('B', 'video.mkv', original, status=JobStatus.IN_PROGRESS)
            db.execute('INSERT INTO jobs VALUES (?, ?)', (sql_id, json.dumps(job.to_record())))
    restored = fresh_recover(path)
    assert restored['jobs']['A']['status'] == 'error'
    assert restored['jobs']['B']['original_path'] == '/source/B'
    assert restored['queue'] == ['B']


def test_recovery_transaction_rollback_does_not_create_partial_queue(tmp_path):
    store = JobStore(str(tmp_path/'jobs.sqlite3'))
    job = make_job(store, 'A')
    store.claim_job(job)
    with sqlite3.connect(store.path) as db:
        db.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON job_queue BEGIN SELECT RAISE(ABORT, 'disk failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.recover()
    with sqlite3.connect(store.path) as db:
        record = json.loads(db.execute('SELECT record FROM jobs').fetchone()[0])
        assert record['status'] == 'in_progress'
        assert record['attempt_no'] == 1
        assert db.execute('SELECT count(*) FROM job_attempts').fetchone()[0] == 1
        db.execute('DROP TRIGGER fail_recovery')
    assert fresh_recover(store.path)['jobs']['A']['attempt_no'] == 2


def test_legacy_migration_queue_order_and_corrupt_record(tmp_path):
    path = tmp_path/'legacy.sqlite3'
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE jobs (id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        for name in ['B', 'A']:
            job = Job(name, 'video.mkv', '/input')
            record = job.to_record()
            for field in ('attempt_no', 'revision', 'recovery_count', 'last_recovered_at', 'dispatch_order'):
                record.pop(field)
            db.execute('INSERT INTO jobs VALUES (?, ?)', (name, json.dumps(record)))
        db.execute("INSERT INTO jobs VALUES ('broken', '{bad')")
    store = JobStore(str(path))
    assert store.queued_ids() == ['B', 'A']  # timestamps создания — детерминированный fallback
    restored = fresh_recover(path)
    assert restored['jobs']['broken']['status'] == 'error'
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT record FROM jobs WHERE id='broken'").fetchone()[0] == '{bad'


@pytest.mark.skipif(os.name != 'posix', reason='POSIX guardian')
def test_parent_death_stops_tool_and_releases_lease(tmp_path):
    path = str(tmp_path/'lease')
    pidfile = tmp_path/'tool.pid'
    child = f"import os,time,pathlib; pathlib.Path({str(pidfile)!r}).write_text(str(os.getpid())); time.sleep(90)"
    script = '''
import subprocess, sys, time
from app.execution import acquire_execution, guarded_command
acquire_execution(sys.argv[1])
cmd, options = guarded_command([sys.executable, '-c', sys.argv[2]])
subprocess.Popen(cmd, **options)
time.sleep(90)
'''
    parent = subprocess.Popen([sys.executable, '-c', script, path, child])
    try:
        deadline = time.monotonic()+10
        while not pidfile.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert pidfile.exists()
        pid = int(pidfile.read_text())
        parent.kill()
        parent.wait(timeout=5)
        subprocess.run([sys.executable, '-c',
                        'from app.execution import acquire_execution; import sys; acquire_execution(sys.argv[1])', path],
                       check=True, timeout=10)
        import psutil
        assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait()


@pytest.mark.skipif(os.name != 'posix', reason='POSIX guardian')
def test_second_server_cannot_acquire_execution(tmp_path):
    script = '''
from app.execution import acquire_execution
import subprocess, sys
acquire_execution(sys.argv[1])
result = subprocess.run([sys.executable, '-c',
    'from app.execution import acquire_execution; import sys; acquire_execution(sys.argv[1], timeout=0)', sys.argv[1]],
    capture_output=True, text=True)
assert result.returncode != 0
assert 'База уже используется' in result.stderr
'''
    subprocess.run([sys.executable, '-c', script, str(tmp_path/'lease')], check=True, timeout=10)
