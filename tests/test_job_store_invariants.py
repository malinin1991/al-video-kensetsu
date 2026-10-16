"""Неизменяемость квитанций и защита истории от запоздавших callbacks."""

from concurrent.futures import ThreadPoolExecutor
import json
import threading

from app.job_store import JobStore
from app.models.job import Job, JobStatus


def test_ready_receipt_cannot_be_overwritten(tmp_path):
    store = JobStore(str(tmp_path / 'jobs.sqlite3'))
    store.claim_request('immutable', 'fingerprint')
    original = {'jobs': [{'id': 'first'}], 'errors': []}
    store.finish_request('immutable', original, 207)
    store.finish_request('immutable', {'error': 'late callback'}, 500)
    store.interrupt_request('immutable')

    receipt = store.get_request('immutable')
    assert receipt['state'] == 'ready'
    assert receipt['status_code'] == 207
    assert json.loads(receipt['body']) == original


def test_interrupted_receipt_cannot_be_finished_late(tmp_path):
    store = JobStore(str(tmp_path / 'jobs.sqlite3'))
    store.claim_request('interrupted', 'fingerprint')
    store.recover()
    store.finish_request('interrupted', {'job': {'id': 'late'}}, 200)

    receipt = store.get_request('interrupted')
    assert receipt['state'] == 'interrupted'
    assert receipt['status_code'] is None
    assert receipt['body'] is None


def test_concurrent_finish_publishes_one_complete_receipt(tmp_path):
    path = str(tmp_path / 'jobs.sqlite3')
    first = JobStore(path)
    first.claim_request('concurrent', 'fingerprint')
    stores = [JobStore(path) for _ in range(8)]
    barrier = threading.Barrier(len(stores))

    def finish(index):
        barrier.wait(timeout=5)
        stores[index].finish_request('concurrent', {'winner': index}, 200 + index)

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        list(pool.map(finish, range(len(stores))))
    receipt = first.get_request('concurrent')
    winner = json.loads(receipt['body'])['winner']
    assert receipt['state'] == 'ready'
    assert receipt['status_code'] == 200 + winner

    for store in stores:
        store.finish_request('concurrent', {'winner': 'too late'}, 500)
    assert first.get_request('concurrent') == receipt


def test_deleted_job_is_not_restored_by_late_save(tmp_path):
    store = JobStore(str(tmp_path / 'jobs.sqlite3'))
    job = Job('deleted', 'movie.mkv', '/unused', request_id='receipt')
    store.insert_job(job)
    store.delete_job(job.id)
    job.status = JobStatus.DONE
    store.save_job(job)

    assert store.recover() == []
    assert store.request_job_ids('receipt') == [job.id]
