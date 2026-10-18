"""SQLite: задания и неизменяемые квитанции internal encode."""

from contextlib import contextmanager
from functools import lru_cache
import json
import os
import sqlite3
import uuid

from app.models.job import Job, JobStatus, utc_now

SCHEMA_VERSION = 1


class StatePersistenceError(RuntimeError):
    """Исполнение приостановлено: состояние не подтверждено постоянным хранилищем."""


def _json(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


class JobStore:
    def __init__(self, path: str):
        self.path = os.path.realpath(os.path.abspath(path))
        self.run_id = uuid.uuid4().hex
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with self._connect() as db:
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version > SCHEMA_VERSION:
                raise RuntimeError('Несовместимая версия SQLite заданий')
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
            # DDL и перенос очереди атомарны; версия 0 — прежняя история без очереди.
            db.execute('BEGIN IMMEDIATE')
            db.execute('CREATE TABLE IF NOT EXISTS job_queue (job_id TEXT PRIMARY KEY, position INTEGER UNIQUE NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS job_attempts (job_id TEXT, attempt_no INTEGER, record TEXT NOT NULL, PRIMARY KEY(job_id, attempt_no))')
            db.execute('CREATE TABLE IF NOT EXISTS job_actions (job_id TEXT PRIMARY KEY, action TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS execution_counter (id INTEGER PRIMARY KEY CHECK(id=1), value INTEGER NOT NULL)')
            db.execute('INSERT OR IGNORE INTO execution_counter VALUES (1, 0)')
            if version == 0:
                records = []
                for row in db.execute('SELECT id, record FROM jobs'):
                    try:
                        job = Job.from_record(json.loads(row['record']))
                        if job.id != row['id']:
                            raise ValueError('ID записи не совпадает')
                    except (ValueError, TypeError, KeyError):
                        continue
                    records.append(job)
                for index, job in enumerate(sorted(records, key=lambda j: (j.created_at.isoformat(), j.id))):
                    if job.status == JobStatus.PLANNED:
                        db.execute('INSERT OR IGNORE INTO job_queue VALUES (?, ?)', (job.id, index))
                    self._save_attempt(db, job)
                db.execute(f'PRAGMA user_version = {SCHEMA_VERSION}')

    @contextmanager
    def _connect(self):
        db = None
        try:
            db = sqlite3.connect(self.path, timeout=30)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        except (OSError, sqlite3.Error) as exc:
            callback = getattr(self, 'failure_callback', None)
            if callback is not None:
                callback(exc)
            raise
        finally:
            if db is not None:
                db.close()

    def insert_job(self, job: Job):
        record = job.to_record()
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('INSERT INTO jobs VALUES (?, ?)', (job.id, _json(record)))
            self._save_attempt(db, job)
            if job.status == JobStatus.PLANNED:
                position = db.execute('SELECT COALESCE(MAX(position), 0)+1 FROM job_queue').fetchone()[0]
                db.execute('INSERT INTO job_queue VALUES (?, ?)', (job.id, position))
            if job.request_id:
                db.execute('INSERT INTO request_jobs VALUES (?, ?)', (job.request_id, job.id))
        job._durable_accepted = True
        job._committed_record = record

    def load_job(self, job_id):
        with self._connect() as db:
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            return None
        job = Job.from_record(json.loads(row['record']))
        if job.id != job_id:
            raise ValueError('ID записи не совпадает')
        return job

    def save_job(self, job: Job):
        # UPDATE не воскрешает удалённое задание из запоздавшего callback.
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job.id,)).fetchone()
            if row is None:
                return False
            previous = json.loads(row['record'])
            if previous.get('attempt_no', 1) != job.attempt_no:
                return False
            if previous['status'] in ('done', 'error', 'cancelled') and previous['status'] != job.status.value:
                return False
            next_revision = previous.get('revision', 1) + 1
            record = job.to_record()
            record['revision'] = next_revision
            db.execute('UPDATE jobs SET record = ? WHERE id = ?', (_json(record), job.id))
            self._save_attempt(db, job, record=record)
            if job.status != JobStatus.PLANNED:
                db.execute('DELETE FROM job_queue WHERE job_id=?', (job.id,))
        job.revision = next_revision
        job._committed_record = record
        return True

    @staticmethod
    def _save_attempt(db, job, *, outcome=None, record=None):
        record = dict(record or job.to_record())
        record['outcome'] = outcome or job.status.value
        db.execute('INSERT OR REPLACE INTO job_attempts VALUES (?, ?, ?)',
                   (job.id, job.attempt_no, _json(record)))

    def queued_ids(self):
        with self._connect() as db:
            return [row[0] for row in db.execute(
                'SELECT q.job_id FROM job_queue q LEFT JOIN job_actions a ON a.job_id=q.job_id '
                'WHERE a.job_id IS NULL ORDER BY q.position')]

    def claim_job(self, job):
        """Выдача и удаление ожидания — один commit до создания потока/Popen."""
        with self._connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT record FROM jobs WHERE id=?', (job.id,)).fetchone()
            if row is None or db.execute('SELECT 1 FROM job_actions WHERE job_id=?', (job.id,)).fetchone():
                return False
            saved = json.loads(row[0])
            if saved['status'] != 'planned' or saved.get('attempt_no', 1) != job.attempt_no:
                return False
            if not db.execute('DELETE FROM job_queue WHERE job_id=?', (job.id,)).rowcount:
                return False
            order = db.execute('UPDATE execution_counter SET value=value+1 WHERE id=1 RETURNING value').fetchone()[0]
            record = job.to_record()
            record.update(status='in_progress', started_at=utc_now().isoformat(),
                          dispatch_order=order, execution_run_id=self.run_id,
                          revision=saved.get('revision', 1)+1)
            db.execute('UPDATE jobs SET record=? WHERE id=?', (_json(record), job.id))
            self._save_attempt(db, job, record=record)
        updated = Job.from_record(record)
        job.__dict__.update(updated.__dict__)
        return True

    def set_action(self, job_id, action):
        if action not in ('stop', 'delete'):
            raise ValueError('Неизвестное действие')
        with self._connect() as db:
            db.execute('INSERT INTO job_actions VALUES (?, ?) ON CONFLICT(job_id) DO UPDATE '
                       "SET action=CASE WHEN job_actions.action='delete' THEN 'delete' ELSE excluded.action END",
                       (job_id, action))

    def actions(self):
        with self._connect() as db:
            return dict(db.execute('SELECT job_id, action FROM job_actions'))

    def finish_action(self, job_id, expected='stop'):
        with self._connect() as db:
            db.execute('DELETE FROM job_actions WHERE job_id=? AND action=?', (job_id, expected))

    def delete_job(self, job_id: str, *, discard_request_link=False):
        with self._connect() as db:
            db.execute('DELETE FROM jobs WHERE id = ?', (job_id,))
            db.execute('DELETE FROM job_queue WHERE job_id=?', (job_id,))
            db.execute('DELETE FROM job_actions WHERE job_id=?', (job_id,))
            db.execute('DELETE FROM job_attempts WHERE job_id=?', (job_id,))
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
        # Только владелец незавершённой резервации может опубликовать квитанцию.
        # Повторный/запоздавший вызов не меняет уже готовый ответ или interrupted.
        with self._connect() as db:
            db.execute(
                "UPDATE encode_requests SET state = 'ready', body = ?, status_code = ? "
                "WHERE request_id = ? AND state = 'processing'",
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
            jobs = []
            for row in db.execute('SELECT id, record FROM jobs'):
                try:
                    job = Job.from_record(json.loads(row['record']))
                    if job.id != row['id']:
                        raise ValueError('ID записи не совпадает')
                except (ValueError, TypeError, KeyError):
                    # Одна повреждённая запись не блокирует историю и диагностику.
                    job = Job(id=row['id'], filename='Повреждённая запись задания',
                              original_path='', ffmpeg_params={},
                              preset_snapshot={'legacy': True}, status=JobStatus.ERROR,
                              error_message='Не удалось восстановить запись задания')
                jobs.append(job)
            actions = dict(db.execute('SELECT job_id, action FROM job_actions'))
            active = sorted((j for j in jobs if j.status == JobStatus.IN_PROGRESS and j.id not in actions),
                            key=lambda j: (j.dispatch_order or 0, j.created_at.isoformat(), j.id))
            front = db.execute('SELECT COALESCE(MIN(position), 0) FROM job_queue').fetchone()[0] - len(active)
            for index, job in enumerate(active):
                self._save_attempt(db, job, outcome='interrupted')
                job.attempt_no += 1
                job.recovery_count += 1
                job.revision += 1
                job.last_recovered_at = utc_now()
                job.status = JobStatus.PLANNED
                job.progress = 0.0
                job.started_at = job.completed_at = None
                job.total_frames = None
                job.error_message = None
                job.dispatch_order = None
                job.execution_run_id = None
                db.execute('INSERT OR REPLACE INTO job_queue VALUES (?, ?)', (job.id, front+index))
                self._save_attempt(db, job)
                db.execute('UPDATE jobs SET record=? WHERE id=?', (_json(job.to_record()), job.id))
            for job in jobs:
                if job.id in actions:
                    db.execute('DELETE FROM job_queue WHERE job_id=?', (job.id,))
                    if job.status not in (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED):
                        job.status = JobStatus.CANCELLED
                        job.completed_at = utc_now()
                        job.error_message = 'Задание отменено пользователем'
                        job.revision += 1
                        self._save_attempt(db, job)
                        db.execute('UPDATE jobs SET record=? WHERE id=?', (_json(job.to_record()), job.id))
                elif job.status != JobStatus.PLANNED:
                    db.execute('DELETE FROM job_queue WHERE job_id=?', (job.id,))
        for job in jobs:
            job._committed_record = job.to_record()
        return jobs


@lru_cache(maxsize=32)
def _store_at_path(path: str) -> JobStore:
    return JobStore(path)


def get_job_store() -> JobStore:
    return _store_at_path(get_job_store_path())


def get_job_store_path():
    path = os.environ.get('JOB_STORE_PATH') or '/app/settings/jobs.sqlite3'
    return os.path.realpath(os.path.abspath(path))
