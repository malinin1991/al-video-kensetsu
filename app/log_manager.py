from collections import deque
import re
import threading
import time
from typing import Callable


class LogManager:
    """In-memory хранилище логов: системный поток и логи по заданиям."""

    def __init__(
        self,
        max_system_logs: int = 5000,
        max_job_logs: int = 10000,
    ):
        self._lock = threading.Lock()
        self.max_system_logs = max_system_logs
        self.max_job_logs = max_job_logs
        # Системный поток и per-job ограничены maxlen (RAM при длинных encode).
        self.system_logs: deque = deque(maxlen=max_system_logs)
        self.job_logs: dict[str, deque] = {}
        self._emit_callback: Callable[[dict], None] | None = None

    def set_emit_callback(self, callback: Callable[[dict], None]):
        """Регистрирует callback для WebSocket-рассылки новых записей."""
        self._emit_callback = callback

    def _emit(self, entry: dict):
        if self._emit_callback:
            try:
                self._emit_callback(entry)
            except Exception:
                import logging
                logging.getLogger('video_encoder').exception(
                    'Ошибка WebSocket emit для log entry'
                )

    def _make_entry(
        self,
        stream: str,
        job_id: str | None,
        message: str,
        level: str = 'INFO',
        logger_name: str = 'system',
    ) -> dict:
        ts = time.time()
        return {
            'timestamp': ts,
            'time': time.strftime('%H:%M:%S', time.localtime(ts)),
            'stream': stream,
            'jobId': job_id or 'system',
            'job_id': job_id,
            'message': message,
            'level': level,
            'logger': logger_name,
        }

    def add_system_log(
        self,
        message: str,
        level: str = 'INFO',
        logger_name: str = 'system',
        emit: bool = True,
    ) -> dict:
        entry = self._make_entry('system', None, message, level, logger_name)
        with self._lock:
            self.system_logs.append(entry)
        if emit:
            self._emit(entry)
        return entry

    def add_job_log(
        self,
        job_id: str,
        message: str,
        level: str = 'INFO',
        logger_name: str = 'ffmpeg',
        emit: bool = True,
    ) -> dict:
        entry = self._make_entry('job', job_id, message, level, logger_name)
        with self._lock:
            if job_id not in self.job_logs:
                self.job_logs[job_id] = deque(maxlen=self.max_job_logs)
            self.job_logs[job_id].append(entry)
        if emit:
            self._emit(entry)
        return entry

    def add_log(self, message: str, level: str = 'INFO', logger_name: str = 'system') -> dict:
        """Маршрутизирует запись logging в system или job-поток по содержимому."""
        job_id = self._extract_job_id(message)
        if job_id:
            return self.add_job_log(job_id, message, level, logger_name)
        return self.add_system_log(message, level, logger_name)

    @staticmethod
    def _extract_job_id(message: str) -> str | None:
        ffmpeg_prefix = re.match(r'FFmpeg\s+([a-f0-9-]+)', message, re.IGNORECASE)
        if ffmpeg_prefix:
            return ffmpeg_prefix.group(1)

        job_match = re.search(r'задани[ея]\s+([a-f0-9-]{36})', message, re.IGNORECASE)
        if job_match:
            return job_match.group(1)

        return None

    def get_system_logs(self, limit: int | None = None) -> list:
        with self._lock:
            logs = list(self.system_logs)
        if limit is None:
            return logs
        return logs[-limit:]

    def get_job_logs(self, job_id: str, limit: int | None = None) -> list:
        with self._lock:
            if job_id not in self.job_logs:
                return []
            logs = list(self.job_logs[job_id])
        if limit is None:
            return logs
        return logs[-limit:]

    def get_job_ids(self) -> list:
        with self._lock:
            return list(self.job_logs.keys())

    def get_all_job_ids(self) -> list:
        return self.get_job_ids()

    def clear_system_logs(self):
        with self._lock:
            self.system_logs.clear()

    def clear_job_logs(self, job_id: str):
        with self._lock:
            if job_id in self.job_logs:
                self.job_logs[job_id].clear()

    def remove_job_logs(self, job_id: str):
        """Полностью удаляет логи задания (при удалении задания из системы)."""
        with self._lock:
            self.job_logs.pop(job_id, None)

log_manager = LogManager()
