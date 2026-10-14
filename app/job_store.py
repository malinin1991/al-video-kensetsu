"""SQLite: задания и неизменяемые квитанции internal encode."""

from contextlib import contextmanager
from functools import lru_cache
import json
import os
import sqlite3

from app.models.job import Job, JobStatus, utc_now


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class JobStore:
    def __init__(self, path: str):
        self.path = path
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY,
                    record TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS encode_requests (
                    request_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    state TEXT NOT NULL,
                    body TEXT,
                    status_code INTEGER
                );
                CREATE TABLE IF NOT EXISTS request_jobs (
                    request_id TEXT NOT NULL,
                    job_id TEXT NOT NULL,
                    PRIMARY KEY (request_id, job_id)
                );
            ''')

    @contextmanager
    def _connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def insert_job(self, job: Job):
        with self._connect() as db:
            db.execute('INSERT INTO jobs VALUES (?, ?)', (job.id, _json(job.to_record())))
            if job.request_id:
                db.execute('INSERT INTO request_jobs VALUES (?, ?)', (job.request_id, job.id))

    def save_job(self, job: Job):
        # UPDATE не воскрешает удалённое задание из запоздавшего callback.
        with self._connect() as db:
            db.execute('UPDATE jobs SET record = ? WHERE id = ?', (_json(job.to_record()), job.id))

    def delete_job(self, job_id: str, *, discard_request_link=False):
        with self._connect() as db:
            db.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
            if discard_request_link:
                db.execute('DELETE FROM request_jobs WHERE job_id = ?', (job_id,))

    def claim_request(self, request_id: str, fingerprint: str) -> tuple[bool, dict]:
        """Резервирует ID до копирования; UNIQUE + транзакция выбирают одного владельца."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            inserted = db.execute(
                "INSERT OR IGNORE INTO encode_requests VALUES (?, ?, 'processing', NULL, NULL)",
                (request_id, fingerprint),
            ).rowcount == 1
            row = db.execute('SELECT * FROM encode_requests WHERE request_id = ?', (request_id,)).fetchone()
            return inserted, dict(row)

    def finish_request(self, request_id: str, body: dict, status_code: int):
        with self._connect() as db:
            db.execute(
                "UPDATE encode_requests SET state = 'ready', body = ?, status_code = ? WHERE request_id = ?",
                (_json(body), status_code, request_id),
            )

    def interrupt_request(self, request_id: str):
        with self._connect() as db:
            db.execute(
                "UPDATE encode_requests SET state = 'interrupted' WHERE request_id = ? AND state = 'processing'",
                (request_id,),
            )

    def get_request(self, request_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute('SELECT * FROM encode_requests WHERE request_id = ?', (request_id,)).fetchone()
            return dict(row) if row else None

    def request_job_ids(self, request_id: str) -> list[str]:
        with self._connect() as db:
            return [row['job_id'] for row in db.execute(
                'SELECT job_id FROM request_jobs WHERE request_id = ? ORDER BY rowid', (request_id,)
            )]

    def recover(self) -> list[Job]:
        """Вызывать один раз при старте одного серверного процесса, до приёма запросов."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE encode_requests SET state = 'interrupted' WHERE state = 'processing'")
            jobs = [Job.from_record(json.loads(row['record'])) for row in db.execute('SELECT record FROM jobs')]
            for job in jobs:
                if job.status in (JobStatus.PLANNED, JobStatus.IN_PROGRESS):
                    job.status = JobStatus.ERROR
                    job.error_message = 'Задание прервано перезапуском сервера; автоматический повтор отключён'
                    job.completed_at = utc_now()
                    db.execute('UPDATE jobs SET record = ? WHERE id = ?', (_json(job.to_record()), job.id))
            return jobs


@lru_cache(maxsize=32)
def _store_at_path(path: str) -> JobStore:
    return JobStore(path)


def get_job_store() -> JobStore:
    path = os.environ.get('JOB_STORE_PATH') or '/app/settings/jobs.sqlite3'
    return _store_at_path(os.path.abspath(path))
