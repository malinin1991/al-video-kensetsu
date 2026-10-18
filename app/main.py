from flask import Flask, request
from flask_socketio import SocketIO
import importlib.util
import logging
import os
import sys
from app.routes import main_bp
from app.ffmpeg_worker import FFmpegWorker

_log = logging.getLogger(__name__)

# Создаем Flask приложение
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 * 1024  # 16GB максимум
app.config['PRESET_EDITOR_PUBLIC_ORIGIN'] = os.getenv('PRESET_EDITOR_PUBLIC_ORIGIN', '')

# Настройка CORS для Flask
from flask_cors import CORS
cors_origins = [
    origin.strip()
    for origin in os.getenv(
        'CORS_ORIGINS',
        'http://localhost:3000,http://127.0.0.1:3000,'
        'http://localhost:5000,http://127.0.0.1:5000,'
        'http://172.18.0.1:3000,http://pc13.local:3000'
    ).split(',')
    if origin.strip()
]
CORS(app, origins=cors_origins, supports_credentials=True)


def _resolve_socketio_async_mode() -> str:
    """Выбирает async_mode для SocketIO; при неверном значении — threading."""
    requested = (os.getenv('SOCKETIO_ASYNC_MODE') or 'threading').strip().lower()
    allowed = {'threading'}
    if importlib.util.find_spec('gevent') is not None:
        allowed.add('gevent')

    if requested in allowed:
        return requested

    if requested == 'gevent':
        msg = (
            "SOCKETIO_ASYNC_MODE=gevent, но пакет gevent не установлен. "
            "Используется threading."
        )
    else:
        msg = (
            f"SOCKETIO_ASYNC_MODE={requested!r} недопустим "
            f"(eventlet удалён; доступны: {', '.join(sorted(allowed))}). "
            "Используется threading."
        )
    _log.warning(msg)
    print(f"[socketio] WARNING: {msg}", flush=True)
    return 'threading'


# Настраиваем SocketIO для real-time обновлений
# threading: без eventlet; gevent — только если установлен; подходит для single-worker
_socketio_async_mode = _resolve_socketio_async_mode()
print(
    f"[socketio] async_mode={_socketio_async_mode}; Python {sys.version.split()[0]}",
    flush=True,
)
socketio = SocketIO(app,
                   async_mode=_socketio_async_mode,
                   cors_allowed_origins=cors_origins,
                   allow_unsafe_headers=True,
                   logger=True,
                   engineio_logger=True,
                   always_connect=True,
                   ping_timeout=60,
                   ping_interval=25)

# Каталоги данных создают их владельцы по фактическим путям из env:
# uploads при загрузке, outputs перед encode, logger/store/settings при записи.

# Создаем воркер для FFmpeg
def progress_callback(job_id, progress):
    """Callback для обновления прогресса"""
    from app.routes import get_job
    job = get_job(job_id)
    if job:
        job.progress = progress
    else:
        return
    try:
        socketio.emit('progress_update', {
            'job_id': job_id,
            'progress': progress,
            'attempt_no': job.attempt_no,
            'revision': job.revision,
        })
    except Exception:
        _log.warning('Не удалось отправить прогресс задания %s', job_id, exc_info=True)

def _emit_log_update(entry):
    """Отправляет запись лога через WebSocket."""
    socketio.emit('log_update', {
        'stream': entry['stream'],
        'job_id': entry.get('job_id'),
        'message': entry['message'],
        'timestamp': entry['timestamp'],
        'level': entry.get('level', 'INFO'),
    })


def log_callback(job_id, message):
    """Callback для обновления логов задания (stdout FFmpeg)."""
    from app.log_manager import log_manager
    log_manager.add_job_log(job_id, message)

def status_callback(job_id, status, error_message):
    """Callback для обновления статуса задания"""
    from app.routes import get_job
    job = get_job(job_id)
    if job:
        job.status = status
        if error_message:
            job.error_message = error_message
        from app.job_store import get_job_store
        if not get_job_store().save_job(job):
            raise RuntimeError('Устаревшая попытка или удалённое задание')
        try:
            socketio.emit('job_update', job.to_dict())
        except Exception:
            _log.warning('Job %s сохранён, но отправка обновления не удалась', job.id, exc_info=True)

from app.job_store import get_job_store, get_job_store_path
from app.execution import acquire_execution
from app.routes import get_jobs_storage
_execution_path = get_job_store_path()
os.makedirs(os.path.dirname(_execution_path), exist_ok=True)
acquire_execution(_execution_path)
_execution_store = get_job_store()
ffmpeg_worker = FFmpegWorker(progress_callback, log_callback, status_callback,
                             store=_execution_store, jobs=get_jobs_storage(), autostart=False)

from app.log_manager import log_manager
log_manager.set_emit_callback(_emit_log_update)

# Устанавливаем воркер в routes после регистрации маршрутов

# Регистрируем маршруты
app.register_blueprint(main_bp)
from app.preset_routes import preset_editor_bp
from app.preset_catalog import CatalogError
from app.routes import _preset_error_response
app.register_blueprint(preset_editor_bp)
app.register_error_handler(CatalogError, _preset_error_response)

# Устанавливаем воркер в routes
from app.routes import restore_jobs, set_ffmpeg_worker
set_ffmpeg_worker(ffmpeg_worker)
restore_jobs()
_restored_jobs = list(get_jobs_storage().values())
_log.info('Восстановление: ожидают=%s, повторены активные=%s, терминальные=%s, очередь=%s',
          sum(j.status.value == 'planned' for j in _restored_jobs),
          sum(j.status.value == 'planned' and j.last_recovered_at is not None for j in _restored_jobs),
          sum(j.status.value in ('done', 'error', 'cancelled') for j in _restored_jobs),
          _execution_store.queued_ids())
from app.internal_source import get_uploads_dir
from app.file_utils import get_outputs_dir
for _directory in (get_uploads_dir(), get_outputs_dir()):
    if not os.path.isdir(_directory) and any(
            job.status.value in ('planned', 'in_progress') for job in get_jobs_storage().values()):
        raise RuntimeError(f'Хранилище недоступно: {_directory}')
    os.makedirs(_directory, exist_ok=True)
    if not os.access(_directory, os.R_OK | os.W_OK | os.X_OK):
        raise RuntimeError(f'Нет доступа к хранилищу: {_directory}')
ffmpeg_worker.start_worker()


@app.before_request
def reject_encode_when_unavailable():
    if request.method == 'POST' and request.path in ('/api/upload', '/api/internal/encode'):
        if ffmpeg_worker.storage_error or ffmpeg_worker.stop_event.is_set():
            return {'error': 'Исполнение приостановлено; проверьте хранилище и перезапустите сервер'}, 503

@app.route('/health')
def health_check():
    """Проверка здоровья приложения"""
    from app.routes import get_all_jobs
    jobs = get_all_jobs()
    if ffmpeg_worker.storage_error or ffmpeg_worker.stop_event.is_set():
        return {'status': 'unavailable', 'error': ffmpeg_worker.storage_error or 'Сервер останавливается'}, 503
    return {'status': 'healthy', 'jobs_count': len(jobs)}

@socketio.on('connect')
def handle_connect():
    """Обработчик подключения WebSocket"""
    # Начальную историю получает только новое соединение. Изменения заданий
    # по-прежнему рассылаются всем клиентам через callbacks.
    from app.routes import get_all_jobs
    jobs = get_all_jobs()
    for job in jobs:
        socketio.emit('job_update', job.to_dict(), to=request.sid)

@socketio.on('disconnect')
def handle_disconnect():
    """Обработчик отключения WebSocket"""
    pass

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)
