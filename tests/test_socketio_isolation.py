"""Новый клиент получает начальную историю без повторной рассылки остальным."""

from app.job_store import get_job_store
from app.main import app, socketio, status_callback
from app.models.job import Job, JobStatus
import app.routes as routes
import pytest


@pytest.mark.parametrize('origin', [
    'http://localhost:3000', 'http://127.0.0.1:3000',
    'http://localhost:5000', 'http://127.0.0.1:5000',
])
def test_default_origins_allow_compose_and_local_server(origin):
    with app.test_client() as client:
        response = client.get('/socket.io/?EIO=4&transport=polling', headers={'Origin': origin})
        assert response.status_code == 200
        assert response.data.startswith(b'0')
        response.close()


def test_connect_snapshot_is_private_and_status_updates_are_broadcast(monkeypatch):
    job = Job('socket-job', 'video.mkv', '/unused')
    monkeypatch.setattr(routes, 'jobs_storage', {job.id: job})
    get_job_store().insert_job(job)
    first = socketio.test_client(app)
    second = None
    try:
        first_events = first.get_received()
        assert [event['args'][0]['id'] for event in first_events if event['name'] == 'job_update'] == [job.id]
        second = socketio.test_client(app)
        second_events = second.get_received()
        assert [event['args'][0]['id'] for event in second_events if event['name'] == 'job_update'] == [job.id]
        assert first.get_received() == []

        status_callback(job.id, JobStatus.IN_PROGRESS, None)
        for client in (first, second):
            events = client.get_received()
            updates = [event['args'][0] for event in events if event['name'] == 'job_update']
            assert len(updates) == 1
            assert updates[0]['id'] == job.id
            assert updates[0]['status'] == 'in_progress'
    finally:
        if second is not None:
            second.disconnect()
        first.disconnect()
