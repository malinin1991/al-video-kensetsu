from flask import (
    Blueprint,
    after_this_request,
    render_template,
    request,
    jsonify,
    send_file,
)
from werkzeug.utils import secure_filename
import hashlib
import json
import os
import shutil
import tempfile
import uuid
import zipfile
import re
from app.models.job import Job, JobStatus, utc_now
from app.job_store import get_job_store
from app.models.presets import (
    DEFAULT_AUDIO_ID,
    DEFAULT_VIDEO_ID,
    preset_manager,
    validate_audio_downmix_for_params,
)
from app.file_utils import (
    allowed_file, calculate_file_hash,
    create_secure_filename, ensure_directory_exists, cleanup_file
)
from app.internal_source import (
    InternalSourceError,
    bind_original_to_job,
    cleanup_job_original,
    copy_internal_source_to_uploads,
    get_internal_source_roots,
    get_uploads_dir,
    is_internal_source_path,
    original_in_use_by_jobs,
    resolve_internal_source_path,
)
from app.logger import logger
from app.settings_store import (
    DEFAULT_CONCURRENT_ENCODES,
    MAX_CONCURRENT_ENCODES_LIMIT,
    MIN_CONCURRENT_ENCODES,
    get_max_concurrent_encodes,
    get_metadata_nickname,
    has_saved_max_concurrent,
    has_saved_nickname,
    load_settings,
    normalize_max_concurrent_encodes,
    save_settings,
)
from app.mkvmerge_remux import (
    cleanup_job_sidecars,
    get_mkvmerge_bin,
    sanitize_nickname,
    sanitize_video_title,
)
from app.ffmpeg_bootstrap import (
    get_ffmpeg_bin,
    get_ffmpeg_build_info,
    get_ffmpeg_version_label,
    get_ffmpeg_version_token,
    has_libfdk_aac,
)
# Импорты будут определены в функциях для избежания циклических зависимостей

main_bp = Blueprint('main', __name__)

# Глобальные переменные для хранения данных
jobs_storage = {}
ffmpeg_worker = None

def get_jobs_storage():
    global jobs_storage
    return jobs_storage

def set_ffmpeg_worker(worker):
    """Устанавливает воркер FFmpeg"""
    global ffmpeg_worker
    ffmpeg_worker = worker

def get_ffmpeg_worker_instance():
    global ffmpeg_worker
    if ffmpeg_worker is None:
        # Импортируем воркер из main.py
        try:
            from app.main import ffmpeg_worker as main_worker
            ffmpeg_worker = main_worker
        except ImportError:
            # Fallback на dummy воркер если main.py не доступен
            from app.ffmpeg_worker import FFmpegWorker
            def dummy_progress_callback(job_id, progress):
                pass
            def dummy_log_callback(job_id, message):
                pass
            def dummy_status_callback(job_id, status, error_message):
                pass
            ffmpeg_worker = FFmpegWorker(dummy_progress_callback, dummy_log_callback, dummy_status_callback)
    return ffmpeg_worker

def _emit_job_update(job: Job):
    """Отправляет клиентам обновление задания через SocketIO."""
    try:
        from app.main import socketio
        socketio.emit('job_update', job.to_dict())
    except Exception as e:
        logger.main_logger.warning(f"Не удалось отправить job_update для {job.id}: {e}")


def add_job(job: Job):
    """Добавляет новое задание"""
    jobs_storage = get_jobs_storage()
    # Генерируем уникальный ID если его нет
    if not job.id:
        job.id = str(uuid.uuid4())
    
    # Логируем создание задания
    logger.log_job_created(job.id, job.filename, job.status.value)
    
    store = get_job_store()
    store.insert_job(job)
    jobs_storage[job.id] = job
    try:
        ffmpeg_worker = get_ffmpeg_worker_instance()
        ffmpeg_worker.add_job(job)
    except Exception:
        jobs_storage.pop(job.id, None)
        store.delete_job(job.id, discard_request_link=True)
        raise

    # Сразу уведомляем UI (до старта кодирования status_callback ещё не вызывается)
    _emit_job_update(job)
    
    logger.main_logger.info(f"Задание {job.id} добавлено в очередь FFmpeg")
    return job

def get_job(job_id: str) -> Job:
    """Получает задание по ID"""
    jobs_storage = get_jobs_storage()
    return jobs_storage.get(job_id)

def get_all_jobs():
    """Получает все задания"""
    jobs_storage = get_jobs_storage()
    return list(jobs_storage.values())

def update_job_status(job_id: str, status: JobStatus, error_message: str = None):
    """Обновляет статус задания"""
    jobs_storage = get_jobs_storage()
    if job_id in jobs_storage:
        jobs_storage[job_id].status = status
        if error_message:
            jobs_storage[job_id].error_message = error_message
        get_job_store().save_job(jobs_storage[job_id])


def restore_jobs():
    """Восстанавливает историю без автоматического запуска прерванных заданий."""
    jobs_storage.update({job.id: job for job in get_job_store().recover()})

@main_bp.route('/')
def index():
    """Главная страница с формой загрузки"""
    return render_template(
        'index.html',
        metadata_nickname_default=get_metadata_nickname(),
    )


@main_bp.route('/api/settings/metadata', methods=['GET'])
def api_get_metadata_settings():
    """Возвращает сохранённые настройки метаданных."""
    return jsonify({
        'metadata_nickname': get_metadata_nickname(),
        'saved_nickname': load_settings().get('metadata_nickname'),
        'nickname_from_file': has_saved_nickname(),
    })


@main_bp.route('/api/settings/metadata', methods=['PUT', 'POST'])
def api_save_metadata_settings():
    """Сохраняет ник для переименования дорожек (персистентно на диске)."""
    payload = request.get_json(silent=True) or {}
    nickname = payload.get('metadata_nickname')
    if nickname is None:
        nickname = request.form.get('metadata_nickname', '')
    try:
        nickname = sanitize_nickname(str(nickname))
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    saved = save_settings({'metadata_nickname': nickname})
    logger.main_logger.info(
        f"Сохранён ник для метаданных: {saved.get('metadata_nickname')!r}"
    )
    return jsonify({
        'message': 'Ник сохранён',
        'metadata_nickname': saved.get('metadata_nickname') or '',
        'nickname_from_file': True,
    })


def _app_settings_payload() -> dict:
    """Текущие настройки приложения для API/UI."""
    effective = get_max_concurrent_encodes()
    worker = get_ffmpeg_worker_instance()
    runtime = getattr(worker, 'max_concurrent', effective)
    return {
        'max_concurrent_encodes': effective,
        'max_concurrent_encodes_runtime': runtime,
        'max_concurrent_encodes_from_file': has_saved_max_concurrent(),
        'min_concurrent_encodes': MIN_CONCURRENT_ENCODES,
        'max_concurrent_encodes_limit': MAX_CONCURRENT_ENCODES_LIMIT,
        'default_concurrent_encodes': DEFAULT_CONCURRENT_ENCODES,
    }


@main_bp.route('/api/settings', methods=['GET'])
def api_get_app_settings():
    """Возвращает настройки приложения (параллельность и др.)."""
    return jsonify(_app_settings_payload())


@main_bp.route('/api/settings', methods=['PUT', 'POST'])
def api_save_app_settings():
    """Сохраняет настройки приложения и применяет лимит к воркеру без рестарта."""
    payload = request.get_json(silent=True) or {}
    if 'max_concurrent_encodes' not in payload and 'max_concurrent_encodes' not in request.form:
        return jsonify({'error': 'Укажите max_concurrent_encodes'}), 400

    raw = payload.get('max_concurrent_encodes')
    if raw is None:
        raw = request.form.get('max_concurrent_encodes')

    try:
        value = normalize_max_concurrent_encodes(raw)
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400

    saved = save_settings({'max_concurrent_encodes': value})
    worker = get_ffmpeg_worker_instance()
    runtime = worker.set_max_concurrent(saved['max_concurrent_encodes'])
    logger.main_logger.info(
        f'Сохранён лимит параллельных кодирований: {runtime}'
    )
    body = _app_settings_payload()
    body['message'] = 'Настройки сохранены'
    body['max_concurrent_encodes_runtime'] = runtime
    return jsonify(body)


@main_bp.route('/settings')
def settings_page():
    """Страница настроек приложения."""
    return render_template(
        'settings.html',
        max_concurrent_encodes=get_max_concurrent_encodes(),
        min_concurrent=MIN_CONCURRENT_ENCODES,
        max_concurrent_limit=MAX_CONCURRENT_ENCODES_LIMIT,
    )


@main_bp.route('/logs')
def logs():
    """Страница с логами"""
    return render_template('logs.html')

@main_bp.route('/systeminfo')
def systeminfo():
    """Страница с информацией о системе"""
    return render_template('systeminfo.html')

@main_bp.route('/api/jobs')
def api_jobs():
    """API для получения списка всех заданий"""
    jobs = get_all_jobs()
    return jsonify([job.to_dict() for job in jobs])

@main_bp.route('/api/jobs/<job_id>')
def api_job(job_id):
    """API для получения конкретного задания"""
    job = get_job(job_id)
    if job:
        return jsonify(job.to_dict())
    return jsonify({'error': 'Задание не найдено'}), 404

@main_bp.route('/api/upload', methods=['POST'])
def upload_file():
    """API для загрузки файлов (поддерживает чанки)"""
    try:
        # Проверяем, это чанк или полный файл
        chunk_number = request.form.get('chunk_number')
        total_chunks = request.form.get('total_chunks')
        file_id = request.form.get('file_id')
        
        if chunk_number and total_chunks and file_id:
            # Это чанк файла
            return handle_chunk_upload(request, file_id, int(chunk_number), int(total_chunks))
        else:
            # Это полный файл (старый способ)
            return handle_full_file_upload(request)
    
    except Exception as e:
        return jsonify({'error': f'Ошибка загрузки: {str(e)}'}), 500

def _collect_upload_files(request):
    """Собирает файлы из multipart: files[] и/или file.

    Returns:
        (files, is_batch)
    """
    named_files = [
        f for f in request.files.getlist('files')
        if f and getattr(f, 'filename', None)
    ]
    single_files = [
        f for f in request.files.getlist('file')
        if f and getattr(f, 'filename', None)
    ]
    if named_files:
        return named_files, True
    if len(single_files) > 1:
        return single_files, True
    if len(single_files) == 1:
        return single_files, False
    return [], False


def _batch_encode_response(jobs: list, errors: list):
    """Формирует ответ batch encode: 200 / 207 / 400."""
    created = len(jobs)
    payload = {
        'created': created,
        'message': f'Создано заданий: {created}',
        'jobs': jobs,
        'errors': errors,
    }
    if created and errors:
        return jsonify(payload), 207
    if created:
        return jsonify(payload), 200
    return jsonify(payload), 400


def handle_full_file_upload(request):
    """Обрабатывает загрузку полного файла или пачки (files / несколько file)."""
    files, is_batch = _collect_upload_files(request)
    if not files:
        logger.upload_logger.warning("Попытка загрузки без файла")
        return jsonify({'error': 'Файл не выбран'}), 400

    parsed, err = _ffmpeg_params_or_error(request)
    if err is not None:
        return err
    ffmpeg_params, video_id, audio_id, audio_downmix = parsed

    if not is_batch:
        file = files[0]
        if not allowed_file(file.filename):
            logger.upload_logger.warning(f"Неподдерживаемый тип файла: {file.filename}")
            return jsonify({'error': 'Неподдерживаемый тип файла'}), 400
        logger.upload_logger.info(f"Начало загрузки файла: {file.filename}")
        filename = create_secure_filename(file.filename)
        return handle_single_video_upload(
            file, filename, ffmpeg_params, video_id, audio_id, audio_downmix
        )

    jobs = []
    errors = []
    for file in files:
        original_name = file.filename or 'unknown'
        file_path = None
        try:
            if not allowed_file(original_name):
                errors.append({
                    'filename': original_name,
                    'error': 'Неподдерживаемый тип файла',
                })
                continue
            filename = create_secure_filename(original_name)
            logger.upload_logger.info(f"Batch: загрузка файла {original_name}")
            unique_filename = f"{uuid.uuid4().hex}_{filename}"
            file_path = os.path.join(get_uploads_dir(), unique_filename)
            file.save(file_path)
            job, job_err = create_job_from_saved_file(
                file_path, filename, ffmpeg_params, video_id,
                audio_id, audio_downmix,
            )
            if job_err:
                cleanup_file(file_path)
                file_path = None
                errors.append({'filename': original_name, 'error': job_err})
            else:
                file_path = None  # файл принадлежит заданию
                jobs.append(job.to_dict())
        except Exception as exc:
            if file_path:
                cleanup_file(file_path)
            errors.append({'filename': original_name, 'error': str(exc)})

    return _batch_encode_response(jobs, errors)

def handle_chunk_upload(request, file_id, chunk_number, total_chunks):
    """Обрабатывает загрузку чанка файла"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Чанк файла не найден'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Пустой чанк файла'}), 400
        
        # Создаем директорию для временных чанков
        temp_dir = os.path.join(get_uploads_dir(), f'temp_{file_id}')
        ensure_directory_exists(temp_dir)
        
        # Сохраняем чанк
        chunk_filename = f'chunk_{chunk_number:04d}'
        chunk_path = os.path.join(temp_dir, chunk_filename)
        file.save(chunk_path)
        
        logger.upload_logger.info(f"Чанк {chunk_number}/{total_chunks} сохранен: {chunk_path}")
        
        # Проверяем, все ли чанки загружены
        if chunk_number == total_chunks - 1:
            # Последний чанк - собираем файл
            return assemble_file_from_chunks(file_id, total_chunks, request)
        else:
            # Еще не все чанки загружены
            return jsonify({
                'message': f'Чанк {chunk_number + 1}/{total_chunks} загружен',
                'progress': ((chunk_number + 1) / total_chunks) * 100,
                'completed': False
            })
    
    except Exception as e:
        logger.upload_logger.error(f"Ошибка загрузки чанка {chunk_number}: {e}")
        return jsonify({'error': f'Ошибка загрузки чанка: {str(e)}'}), 500

def assemble_file_from_chunks(file_id, total_chunks, request):
    """Собирает файл из чанков"""
    try:
        temp_dir = os.path.join(get_uploads_dir(), f'temp_{file_id}')
        final_filename = request.form.get('filename', 'uploaded_file')
        
        # Создаем финальный файл (уникальное имя, не полагаемся на client file_id)
        final_path = os.path.join(
            get_uploads_dir(),
            f"{uuid.uuid4().hex}_{create_secure_filename(final_filename)}",
        )
        
        with open(final_path, 'wb') as final_file:
            for i in range(total_chunks):
                chunk_path = os.path.join(temp_dir, f'chunk_{i:04d}')
                if os.path.exists(chunk_path):
                    with open(chunk_path, 'rb') as chunk_file:
                        shutil.copyfileobj(chunk_file, final_file, length=4 * 1024 * 1024)
                    # Удаляем чанк после использования
                    cleanup_file(chunk_path)
                else:
                    logger.upload_logger.error(f"Чанк {i} не найден: {chunk_path}")
                    return jsonify({'error': f'Чанк {i} не найден'}), 400
        
        # Удаляем временную директорию
        try:
            os.rmdir(temp_dir)
        except:
            pass
        
        logger.upload_logger.info(f"Файл собран из {total_chunks} чанков: {final_path}")
        
        # Проверяем тип файла
        if not allowed_file(final_filename):
            cleanup_file(final_path)
            return jsonify({'error': 'Неподдерживаемый тип файла'}), 400
        
        # Получаем параметры FFmpeg
        parsed, err = _ffmpeg_params_or_error(request)
        if err is not None:
            cleanup_file(final_path)
            return err
        ffmpeg_params, video_id, audio_id, audio_downmix = parsed
        
        # Обрабатываем одиночный видео файл
        return process_single_video_file(
            final_path, final_filename, ffmpeg_params, video_id,
            audio_id, audio_downmix,
        )
    
    except Exception as e:
        logger.upload_logger.error(f"Ошибка сборки файла из чанков: {e}")
        return jsonify({'error': f'Ошибка сборки файла: {str(e)}'}), 500

def _form_optional(request, key: str) -> str | None:
    """Опциональное поле формы: нет ключа / пусто → None."""
    if key not in request.form:
        return None
    value = request.form.get(key)
    if value is None or str(value).strip() == '':
        return None
    return value


def _form_required(request, key: str) -> str:
    value = _form_optional(request, key)
    if not value:
        raise ValueError(f'Обязательный параметр формы не задан: {key}')
    return value


def _get_form_ffmpeg_params(request):
    """Извлекает параметры FFmpeg из полей формы (без серверных дефолтов)."""
    params = {
        'video_map': _form_optional(request, 'video_map'),
        'audio_map': _form_optional(request, 'audio_map'),
        'subtitle_map': _form_optional(request, 'subtitle_map'),
        'data_map': _form_optional(request, 'data_map'),
        'video_codec': _form_required(request, 'video_codec'),
        'subtitle_codec': _form_optional(request, 'subtitle_codec'),
        'data_codec': _form_optional(request, 'data_codec'),
        'preset': _form_optional(request, 'preset'),
        'crf': _form_optional(request, 'crf'),
        'pixel_format': _form_optional(request, 'pixel_format'),
        'x265_params': _form_optional(request, 'x265_params'),
        'tune': _form_optional(request, 'tune'),
        'audio_codec': _form_optional(request, 'audio_codec'),
        'output_format': _form_required(request, 'output_format'),
    }

    audio_bitrate = request.form.get('audio_bitrate')
    audio_sample_rate = request.form.get('audio_sample_rate')
    audio_vbr = request.form.get('audio_vbr')

    if audio_bitrate:
        params['audio_bitrate'] = str(audio_bitrate)
    if audio_sample_rate:
        params['audio_sample_rate'] = str(audio_sample_rate)
    if audio_vbr is not None:
        params['audio_vbr'] = str(audio_vbr)

    return params


def _get_metadata_params(request):
    """Параметры блока «Обновление метаданных»."""
    update_metadata = request.form.get('update_metadata', 'true').lower() in (
        'true', '1', 'yes', 'on',
    )
    form_nickname = str(request.form.get('metadata_nickname') or '').strip()
    # Пустое поле формы → сохранённый ник (не терять после перезагрузки UI)
    nickname = form_nickname if form_nickname else get_metadata_nickname()
    nickname = sanitize_nickname(nickname)

    # Базовое имя видеодорожки: только на текущий пак, в settings не пишем
    video_title = sanitize_video_title(
        str(request.form.get('metadata_video_title') or '')
    )

    # Автосохранение ника с формы, если пользователь ввёл значение, но не нажал «Сохранить»
    if form_nickname and form_nickname != (load_settings().get('metadata_nickname') or ''):
        try:
            save_settings({'metadata_nickname': form_nickname})
        except Exception as exc:
            logger.main_logger.warning(f'Не удалось автосохранить ник: {exc}')

    return {
        'update_metadata': 'true' if update_metadata else 'false',
        'metadata_nickname': nickname,
        'metadata_video_title': video_title,
    }


def get_ffmpeg_params_from_request(request):
    """Извлекает параметры FFmpeg из запроса.

    Raises:
        ValueError: неизвестный video_id/audio_id, недоступный FDK, невалидный ник.

    Returns:
        (params, video_id, audio_id, audio_downmix)
    """
    video_id = request.form.get('video_id', DEFAULT_VIDEO_ID) or DEFAULT_VIDEO_ID
    audio_id = request.form.get('audio_id', DEFAULT_AUDIO_ID) or DEFAULT_AUDIO_ID
    downmix_raw = request.form.get('audio_downmix', 'none')
    preset_modified = request.form.get('preset_modified', 'false') == 'true'
    is_custom = (
        video_id == 'custom'
        or audio_id == 'custom'
        or preset_modified
    )

    if not is_custom:
        params, video_id, audio_id, audio_downmix, _video, audio = (
            preset_manager.resolve_request(video_id, audio_id, downmix_raw)
        )
        _ensure_fdk_available(audio)
        logger.main_logger.info(
            f"Пресет video={video_id} / audio={audio_id} / downmix={audio_downmix}: "
            f"параметры без изменений"
        )
    else:
        params = _get_form_ffmpeg_params(request)
        video_id = 'custom'
        audio_id = 'custom'
        audio_downmix = validate_audio_downmix_for_params(params, downmix_raw)
        params['audio_downmix'] = audio_downmix
        if params.get('audio_codec') == 'libfdk_aac' and not has_libfdk_aac():
            raise ValueError(
                'Кодек libfdk_aac недоступен в текущей сборке FFmpeg '
                '(нужен nonfree FFmpeg с libfdk_aac)'
            )
        logger.main_logger.info(
            f"Пресет custom (modified={preset_modified}): параметры из формы"
        )

    if params.get('audio_codec') == 'opus':
        params['audio_codec'] = 'libopus'

    params['audio_downmix'] = audio_downmix
    params.update({
        'cpu_threads': str(request.form.get('cpu_threads', '0')),
        'thread_queue_size': str(request.form.get('thread_queue_size', '0')),
        'x265_pools': str(request.form.get('x265_pools', '0'))
    })
    params.update(_get_metadata_params(request))

    return params, video_id, audio_id, audio_downmix


def _ensure_fdk_available(audio) -> None:
    """ValueError, если audio-пресет требует libfdk_aac, а кодека нет."""
    if audio and audio.uses_fdk_aac() and not has_libfdk_aac():
        raise ValueError(
            f'Аудио-пресет «{audio.id}» требует libfdk_aac, но энкодер недоступен '
            f'в текущей сборке FFmpeg (смонтируйте nonfree FFmpeg с FDK)'
        )


def _audio_available(audio) -> bool:
    if audio is None:
        return False
    if audio.uses_fdk_aac():
        return has_libfdk_aac()
    return True


def _ffmpeg_params_or_error(request):
    """Обёртка: параметры FFmpeg или JSON 400 при ValueError."""
    try:
        return get_ffmpeg_params_from_request(request), None
    except ValueError as exc:
        return None, (jsonify({'error': str(exc)}), 400)


def create_job_from_saved_file(
    file_path,
    filename,
    ffmpeg_params,
    video_id=DEFAULT_VIDEO_ID,
    audio_id=DEFAULT_AUDIO_ID,
    audio_downmix='none',
    *,
    input_path=None,
    request_id=None,
):
    """Создаёт задание из уже сохранённого файла.

    Returns:
        (Job, None) или (None, error_message)
    """
    bound_path = file_path
    job_added = False
    try:
        logger.upload_logger.info(f"Обработка одиночного файла: {filename}")

        job_id = str(uuid.uuid4())
        shared = original_in_use_by_jobs(file_path, get_all_jobs())
        bound_path = bind_original_to_job(
            file_path, job_id, filename, shared=shared
        )

        file_size = os.path.getsize(bound_path)
        logger.upload_logger.info(f"Файл готов: {bound_path} ({file_size} байт)")

        file_hash = calculate_file_hash(bound_path)
        logger.upload_logger.info(f"Хеш файла вычислен: {file_hash[:16]}...")

        job = Job(
            id=job_id,
            filename=filename,
            original_path=bound_path,
            file_hash=file_hash,
            ffmpeg_params=dict(ffmpeg_params),
            video_id=video_id,
            audio_id=audio_id,
            audio_downmix=audio_downmix,
            input_path=input_path,
            request_id=request_id,
        )

        added_job = add_job(job)
        job_added = True
        logger.upload_logger.info(f"Одиночный файл обработан успешно: задание {added_job.id}")
        return added_job, None
    except Exception as e:
        if not job_added and bound_path and bound_path != file_path:
            cleanup_file(bound_path)
        return None, f'Ошибка загрузки файла: {str(e)}'


def process_single_video_file(
    file_path,
    filename,
    ffmpeg_params,
    video_id=DEFAULT_VIDEO_ID,
    audio_id=DEFAULT_AUDIO_ID,
    audio_downmix='none',
):
    """Общая функция для обработки одиночного видео файла"""
    job, err = create_job_from_saved_file(
        file_path, filename, ffmpeg_params, video_id,
        audio_id, audio_downmix,
    )
    if err:
        return jsonify({'error': err}), 500
    return jsonify({
        'message': 'Файл загружен успешно',
        'job': job.to_dict(),
        'completed': True,
    })

def handle_single_video_upload(
    file,
    filename,
    ffmpeg_params,
    video_id=DEFAULT_VIDEO_ID,
    audio_id=DEFAULT_AUDIO_ID,
    audio_downmix='none',
):
    """Обрабатывает загрузку одиночного видео файла"""
    try:
        logger.upload_logger.info(f"Обработка одиночного файла: {filename}")
        
        # Создаем уникальное имя файла
        unique_filename = f"{uuid.uuid4().hex}_{filename}"
        file_path = os.path.join(get_uploads_dir(), unique_filename)
        
        # Сохраняем файл
        file.save(file_path)
        
        logger.upload_logger.info(f"Файл сохранен: {file_path}")
        
        # Используем общую функцию для обработки одиночного файла
        return process_single_video_file(
            file_path, filename, ffmpeg_params, video_id,
            audio_id, audio_downmix,
        )
    
    except Exception as e:
        return jsonify({'error': f'Ошибка загрузки файла: {str(e)}'}), 500


def _resolve_preset_from_json(payload):
    """video_id + audio_id + downmix из JSON.

    Returns:
        (params, video_id, audio_id, audio_downmix, video, audio)
    """
    video_raw = payload.get('video_id')
    if video_raw is None or str(video_raw).strip() == '':
        video_raw = payload.get('video')
    if video_raw is None or str(video_raw).strip() == '':
        video_raw = DEFAULT_VIDEO_ID

    audio_raw = payload.get('audio_id')
    if audio_raw is None or str(audio_raw).strip() == '':
        audio_raw = payload.get('audio')
    if audio_raw is None or str(audio_raw).strip() == '':
        audio_raw = DEFAULT_AUDIO_ID

    downmix_raw = payload.get('audio_downmix', 'none')
    return preset_manager.resolve_request(
        str(video_raw).strip(),
        str(audio_raw).strip(),
        downmix_raw,
    )


def _ffmpeg_params_from_json(payload):
    """Параметры FFmpeg для JSON API: video + audio + downmix + ник."""
    params, video_id, audio_id, audio_downmix, _video, audio = (
        _resolve_preset_from_json(payload)
    )
    _ensure_fdk_available(audio)
    if params.get('audio_codec') == 'opus':
        params['audio_codec'] = 'libopus'

    params.update({
        'cpu_threads': str(payload.get('cpu_threads', '0')),
        'thread_queue_size': str(payload.get('thread_queue_size', '0')),
        'x265_pools': str(payload.get('x265_pools', '0')),
    })

    nickname_raw = payload.get('metadata_nickname')
    if nickname_raw is None:
        nickname_raw = payload.get('nickname')
    form_nickname = str(nickname_raw or '').strip()
    nickname = form_nickname if form_nickname else get_metadata_nickname()
    nickname = sanitize_nickname(nickname)

    video_title_raw = payload.get('metadata_video_title')
    if video_title_raw is None:
        video_title_raw = payload.get('video_title')
    video_title = sanitize_video_title(str(video_title_raw or ''))

    update_metadata = payload.get('update_metadata', True)
    if isinstance(update_metadata, str):
        update_metadata = update_metadata.lower() in ('true', '1', 'yes', 'on')
    elif update_metadata is None:
        update_metadata = True

    params.update({
        'update_metadata': 'true' if update_metadata else 'false',
        'metadata_nickname': nickname,
        'metadata_video_title': video_title,
        'audio_downmix': audio_downmix,
    })
    return params, video_id, audio_id, audio_downmix


def _unwrap_flask_result(result):
    """Разбирает return jsonify(...) или (response, status)."""
    if isinstance(result, tuple):
        response = result[0]
        status_code = result[1] if len(result) > 1 else 200
        return response, status_code
    return result, getattr(result, 'status_code', 200)


def _collect_internal_paths(is_json, payload, request):
    """Собирает пути для internal encode. Returns (paths, is_batch)."""
    if is_json:
        if 'paths' in payload and payload.get('paths') is not None:
            raw = payload.get('paths')
            if isinstance(raw, str):
                paths = [raw] if raw.strip() else []
            elif isinstance(raw, list):
                paths = [str(p) for p in raw if p is not None and str(p).strip()]
            else:
                raise ValueError('Поле paths должно быть массивом строк')
            return paths, True
        path = payload.get('path')
        if path is None or str(path).strip() == '':
            return [], False
        return [path], False

    form_paths = request.form.getlist('paths')
    form_paths = [p for p in form_paths if p and str(p).strip()]
    if form_paths:
        return form_paths, True
    path = request.form.get('path')
    if path is None or str(path).strip() == '':
        return [], False
    return [path], False


def _encode_internal_single(
    path, ffmpeg_params, video_id, audio_id, audio_downmix, request_id=None
):
    """Копирует один internal-путь и создаёт задание. Возвращает Flask response."""
    dest_path = None
    try:
        resolved = resolve_internal_source_path(path)
        dest_path, original_name = copy_internal_source_to_uploads(resolved)
        job_obj, job_err = create_job_from_saved_file(
            dest_path, original_name, ffmpeg_params, video_id,
            audio_id, audio_downmix,
            input_path=str(path), request_id=request_id,
        )
        if job_err:
            cleanup_file(dest_path)
            return jsonify({'error': job_err}), 500

        encoding_started = bool(
            job_obj and job_obj.status == JobStatus.IN_PROGRESS
        )
        return jsonify({
            'created': True,
            'queued': True,
            'encoding_started': encoding_started,
            'message': 'Задание создано и добавлено в очередь',
            'job': job_obj.to_dict(),
        })
    except InternalSourceError as exc:
        if dest_path:
            cleanup_file(dest_path)
        return jsonify({'error': exc.message}), exc.status_code
    except Exception:
        if dest_path:
            cleanup_file(dest_path)
        raise


def _encode_internal_batch(
    paths, ffmpeg_params, video_id, audio_id, audio_downmix, request_id=None
):
    """Batch internal encode: один video/audio/downmix на все paths."""
    jobs = []
    errors = []
    for path in paths:
        dest_path = None
        display_name = str(path)
        try:
            resolved = resolve_internal_source_path(path)
            display_name = os.path.basename(resolved) or display_name
            dest_path, original_name = copy_internal_source_to_uploads(resolved)
            display_name = original_name
            job, job_err = create_job_from_saved_file(
                dest_path, original_name, ffmpeg_params, video_id,
                audio_id, audio_downmix,
                input_path=str(path), request_id=request_id,
            )
            if job_err:
                cleanup_file(dest_path)
                errors.append({'filename': display_name, 'error': job_err, 'path': str(path)})
            else:
                jobs.append(job.to_dict())
        except InternalSourceError as exc:
            if dest_path:
                cleanup_file(dest_path)
            errors.append({
                'filename': display_name,
                'error': exc.message,
                'path': str(path),
            })
        except Exception as exc:
            if dest_path:
                cleanup_file(dest_path)
            errors.append({
                'filename': display_name,
                'error': str(exc),
                'path': str(path),
            })
    return _batch_encode_response(jobs, errors)


@main_bp.route('/api/internal/encode', methods=['POST'])
def api_internal_encode():
    """Кодирование файла(ов) с примонтированного хранилища (JSON и form)."""
    claimed_id = None
    try:
        if get_internal_source_roots() is None:
            return jsonify({'error': 'Внутренний режим кодирования отключён'}), 403

        is_json = bool(request.is_json)
        payload = {}
        if is_json:
            payload = request.get_json(silent=True) or {}
            if not isinstance(payload, dict):
                return jsonify({'error': 'Некорректный JSON'}), 400

        raw_id = payload.get('request_id') if is_json else request.form.get('request_id')
        if not is_json and len(request.form.getlist('request_id')) > 1:
            return jsonify({'error': 'Укажите один request_id'}), 400
        if raw_id is not None and (
            not isinstance(raw_id, str) or not re.fullmatch(r'[A-Za-z0-9._-]{1,128}', raw_id)
        ):
            return jsonify({'error': 'request_id: 1–128 символов A–Z, a–z, 0–9, ., _, -'}), 400

        if raw_id is not None:
            # Сравниваем переданные параметры до resolve: изменение defaults не меняет повтор.
            submitted = dict(payload) if is_json else request.form.to_dict(flat=False)
            submitted.pop('request_id', None)
            fingerprint = hashlib.sha256(json.dumps(
                {'format': 'json' if is_json else 'form', 'payload': submitted},
                ensure_ascii=False, sort_keys=True, separators=(',', ':'),
            ).encode('utf-8')).hexdigest()
            store = get_job_store()
            claimed, receipt = store.claim_request(raw_id, fingerprint)
            if receipt['fingerprint'] != fingerprint:
                return jsonify({'error': 'request_id уже использован с другими параметрами', 'request_id': raw_id}), 409
            if not claimed:
                return _internal_receipt_response(receipt)
            claimed_id = raw_id

        result = _execute_internal_encode(is_json, payload, raw_id)
        if claimed_id is None:
            return result
        response, status_code = _unwrap_flask_result(result)
        body = response.get_json()
        body.update(request_id=claimed_id, receipt_url=_receipt_url(claimed_id))
        store.finish_request(claimed_id, body, status_code)
        return jsonify(body), status_code
    except Exception as e:
        logger.upload_logger.error(f"Ошибка внутреннего кодирования: {e}")
        if claimed_id:
            try:
                get_job_store().interrupt_request(claimed_id)
            except Exception:
                logger.upload_logger.exception('Не удалось сохранить прерывание приёма')
        return jsonify({'error': f'Ошибка внутреннего кодирования: {str(e)}'}), 500


def _receipt_url(request_id):
    return f'/api/internal/encode/requests/{request_id}'


def _internal_receipt_response(receipt):
    if receipt['state'] == 'ready':
        return jsonify(json.loads(receipt['body'])), receipt['status_code']
    response = jsonify({
        'request_id': receipt['request_id'],
        'request_status': receipt['state'],
        'receipt_url': _receipt_url(receipt['request_id']),
        'job_ids': get_job_store().request_job_ids(receipt['request_id']),
    })
    if receipt['state'] == 'processing':
        response.headers['Retry-After'] = '2'
    return response, 202


@main_bp.route('/api/internal/encode/requests/<request_id>', methods=['GET'])
def api_internal_encode_receipt(request_id):
    """Read-only квитанция: не копирует файлы и не запускает задания."""
    if get_internal_source_roots() is None:
        return jsonify({'error': 'Внутренний режим кодирования отключён'}), 403
    receipt = get_job_store().get_request(request_id)
    if receipt is None:
        return jsonify({'error': 'Квитанция не найдена'}), 404
    return _internal_receipt_response(receipt)


def _execute_internal_encode(is_json, payload, request_id):
    try:

        try:
            paths, is_batch = _collect_internal_paths(is_json, payload, request)
        except ValueError as exc:
            return jsonify({'error': str(exc)}), 400

        if not paths:
            return jsonify({'error': 'Не указан path / paths'}), 400

        # Валидация video/audio/FDK/downmix до создания заданий
        if is_json:
            try:
                ffmpeg_params, video_id, audio_id, audio_downmix = (
                    _ffmpeg_params_from_json(payload)
                )
            except ValueError as exc:
                return jsonify({'error': str(exc)}), 400
        else:
            parsed, err = _ffmpeg_params_or_error(request)
            if err is not None:
                return err
            ffmpeg_params, video_id, audio_id, audio_downmix = parsed

        if is_batch:
            return _encode_internal_batch(
                paths, ffmpeg_params, video_id, audio_id, audio_downmix, request_id
            )
        return _encode_internal_single(
            paths[0], ffmpeg_params, video_id, audio_id, audio_downmix, request_id
        )
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 400
    except Exception as e:
        logger.upload_logger.error(f"Ошибка внутреннего кодирования: {e}")
        return jsonify({'error': f'Ошибка внутреннего кодирования: {str(e)}'}), 500

def _batch_archive_name(job_ids: list[str]) -> str:
    """kensetsu-{blake2b8}.zip по отсортированным job_id через \\n."""
    canonical = '\n'.join(sorted(job_ids))
    hash8 = hashlib.blake2b(
        canonical.encode('utf-8'), digest_size=4
    ).hexdigest()
    return f'kensetsu-{hash8}.zip'


def _unique_zip_arcname(filename: str, job_id: str, used: set[str]) -> str:
    """Имя внутри ZIP; при коллизии — суффикс job_id[:8]."""
    if filename not in used:
        used.add(filename)
        return filename
    name, ext = os.path.splitext(filename)
    candidate = f'{name}_{job_id[:8]}{ext}'
    if candidate in used:
        candidate = f'{name}_{job_id}{ext}'
    used.add(candidate)
    return candidate


@main_bp.route('/api/jobs/download', methods=['POST'])
def download_jobs_batch():
    """Пакетное скачивание готовых файлов в ZIP_STORED (без сжатия)."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'Некорректный JSON'}), 400

    job_ids = payload.get('job_ids')
    if not isinstance(job_ids, list) or not job_ids:
        return jsonify({'error': 'Укажите job_ids (непустой массив)'}), 400
    if not all(isinstance(jid, str) and jid for jid in job_ids):
        return jsonify({'error': 'job_ids должен содержать строки'}), 400

    # Кандидаты: done + output_path; отсутствующие на диске — ошибка без архива
    candidates = []
    missing = []
    for job_id in job_ids:
        job = get_job(job_id)
        if not job or job.status != JobStatus.DONE or not job.output_path:
            continue
        if job.available_output_size() is None:
            missing.append({
                'job_id': job.id,
                'filename': job.get_output_filename(),
                'path': job.output_path,
            })
            continue
        candidates.append(job)

    if missing:
        return jsonify({
            'error': 'Часть выходных файлов отсутствует на диске',
            'missing': missing,
        }), 409

    if not candidates:
        return jsonify({
            'error': 'Нет готовых заданий со скачиваемыми файлами',
        }), 400

    archive_job_ids = [job.id for job in candidates]
    archive_name = _batch_archive_name(archive_job_ids)
    comment = (
        f'kensetsu batch; {len(candidates)} files; '
        f'job_ids: {",".join(sorted(archive_job_ids))}'
    ).encode('utf-8')

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix='.zip')
    tmp_path = tmp.name
    tmp.close()
    try:
        used_names: set[str] = set()
        with zipfile.ZipFile(
            tmp_path, 'w', compression=zipfile.ZIP_STORED
        ) as zf:
            zf.comment = comment
            for job in candidates:
                arcname = _unique_zip_arcname(
                    job.get_output_filename(), job.id, used_names
                )
                zf.write(job.output_path, arcname=arcname)

        @after_this_request
        def _cleanup_zip(response):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            return response

        return send_file(
            tmp_path,
            as_attachment=True,
            download_name=archive_name,
            mimetype='application/zip',
        )
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def delete_job_by_id(job_id: str) -> dict:
    """
    Удаляет одно задание: stop, файлы, storage, логи.
    Возвращает dict с результатом; бросает KeyError если не найдено.
    """
    job = get_job(job_id)
    if not job:
        raise KeyError(job_id)

    ffmpeg_worker = get_ffmpeg_worker_instance()
    stop_result = ffmpeg_worker.stop_job(job_id)
    logger.main_logger.info(f"Удаление задания {job_id}: {stop_result}")

    if job.original_path and is_internal_source_path(job.original_path):
        logger.main_logger.info(
            f"Исходник не удалён (internal): {job.original_path}"
        )
    elif job.original_path and original_in_use_by_jobs(
        job.original_path, get_all_jobs(), job.id
    ):
        logger.main_logger.info(
            f"Исходник не удалён (используется другим заданием): {job.original_path}"
        )
    else:
        cleanup_job_original(job.original_path)

    if job.output_path and os.path.exists(job.output_path):
        cleanup_file(job.output_path)

    if job.output_path:
        cleanup_job_sidecars(job.output_path, job.id)

    jobs_storage = get_jobs_storage()
    get_job_store().delete_job(job_id)
    if job_id in jobs_storage:
        del jobs_storage[job_id]

    from app.log_manager import log_manager
    log_manager.remove_job_logs(job_id)

    return {
        'job_id': job_id,
        'message': 'Задание удалено успешно',
        'stop': stop_result,
    }


@main_bp.route('/api/jobs/delete', methods=['POST'])
def delete_jobs_batch():
    """Пакетное удаление заданий (любой статус)."""
    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'error': 'Некорректный JSON'}), 400

    job_ids = payload.get('job_ids')
    if not isinstance(job_ids, list) or not job_ids:
        return jsonify({'error': 'Укажите job_ids (непустой массив)'}), 400
    if not all(isinstance(jid, str) and jid for jid in job_ids):
        return jsonify({'error': 'job_ids должен содержать строки'}), 400

    deleted = []
    errors = []
    results = []

    for job_id in job_ids:
        try:
            result = delete_job_by_id(job_id)
            deleted.append(job_id)
            results.append(result)
        except KeyError:
            errors.append({'job_id': job_id, 'error': 'Задание не найдено'})
        except Exception as e:
            errors.append({'job_id': job_id, 'error': str(e)})

    body = {
        'deleted': deleted,
        'errors': errors,
        'results': results,
    }
    if not deleted and errors:
        return jsonify(body), 400
    if deleted and errors:
        return jsonify(body), 207
    return jsonify(body), 200


@main_bp.route('/api/jobs/<job_id>/download')
def download_file(job_id):
    """API для скачивания закодированного файла"""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Задание не найдено'}), 404
    
    if job.status != JobStatus.DONE:
        return jsonify({'error': 'Файл еще не готов'}), 400
    
    if job.available_output_size() is None:
        return jsonify({'error': 'Выходной файл не найден'}), 404
    
    try:
        return send_file(
            job.output_path,
            as_attachment=True,
            download_name=job.get_output_filename()
        )
    except OSError:
        return jsonify({'error': 'Выходной файл не найден'}), 404

@main_bp.route('/api/jobs/<job_id>/delete', methods=['DELETE'])
def delete_job(job_id):
    """API для удаления задания и файлов (очередь + остановка FFmpeg при необходимости)"""
    try:
        result = delete_job_by_id(job_id)
        return jsonify(result)
    except KeyError:
        return jsonify({'error': 'Задание не найдено'}), 404
    except Exception as e:
        return jsonify({'error': f'Ошибка удаления: {str(e)}'}), 500

@main_bp.route('/api/jobs/<job_id>/stop', methods=['POST'])
def stop_job(job_id):
    """API для остановки выполнения задания"""
    job = get_job(job_id)
    if not job:
        return jsonify({'error': 'Задание не найдено'}), 404
    
    if job.status not in (JobStatus.IN_PROGRESS, JobStatus.PLANNED):
        return jsonify({'error': 'Задание не выполняется и не в очереди'}), 400
    
    try:
        ffmpeg_worker = get_ffmpeg_worker_instance()
        stop_result = ffmpeg_worker.stop_job(job_id)
        if job.output_path:
            cleanup_job_sidecars(job.output_path, job.id)
        update_job_status(job_id, JobStatus.CANCELLED, 'Отменено пользователем')
        job = get_job(job_id)
        if job:
            if not job.completed_at:
                job.completed_at = utc_now()
            get_job_store().save_job(job)
            _emit_job_update(job)
        
        return jsonify({
            'message': 'Задание отменено',
            'stop': stop_result,
        })
    
    except Exception as e:
        return jsonify({'error': f'Ошибка остановки: {str(e)}'}), 500

@main_bp.route('/api/presets')
def api_presets():
    """API: video + audio слои (subtitles/data — внутренние defaults)."""
    videos = preset_manager.get_all_videos()
    audios = preset_manager.get_all_audios()
    fdk_ok = has_libfdk_aac()
    logger.main_logger.info(
        f"API пресетов: {len(videos)} video, {len(audios)} audio"
    )
    videos_out = [v.to_dict() for v in videos]
    audios_out = []
    for audio in audios:
        available = (not audio.uses_fdk_aac()) or fdk_ok
        audios_out.append(audio.to_dict(available=available))
    return jsonify({
        'videos': videos_out,
        'audios': audios_out,
        'defaults': {
            'video_id': preset_manager.default_video_id,
            'audio_id': preset_manager.default_audio_id,
            'audio_downmix': 'none',
        },
    })


@main_bp.route('/api/presets/resolve')
def api_presets_resolve():
    """Слитый ffmpeg_params для выбранных video_id + audio_id."""
    video_id = request.args.get('video_id') or preset_manager.default_video_id
    audio_id = request.args.get('audio_id') or preset_manager.default_audio_id
    try:
        params = preset_manager.resolve(video_id, audio_id, 'none')
    except ValueError as exc:
        return jsonify({'error': str(exc)}), 404
    video = preset_manager.get_video(video_id)
    audio = preset_manager.get_audio(audio_id)
    return jsonify({
        'video_id': video_id,
        'audio_id': audio_id,
        'available': _audio_available(audio),
        'video': video.to_dict() if video else None,
        'audio': audio.to_dict(available=_audio_available(audio)) if audio else None,
        'ffmpeg_params': params,
    })

@main_bp.route('/api/logs')
def api_logs():
    """API для получения in-memory логов (system или job-поток)."""
    from app.log_manager import log_manager

    try:
        stream = request.args.get('stream')
        job_id = request.args.get('job_id')

        if job_id:
            job = get_job(job_id)
            if not job:
                return jsonify({'error': 'Задание не найдено'}), 404
            return jsonify(log_manager.get_job_logs(job_id))

        if stream == 'job':
            return jsonify({'error': 'Параметр job_id обязателен для stream=job'}), 400

        return jsonify(log_manager.get_system_logs())

    except Exception as e:
        logger.main_logger.error(f"Ошибка получения логов: {e}")
        return jsonify({'error': f'Ошибка получения логов: {str(e)}'}), 500


@main_bp.route('/api/logs/jobs')
def api_log_job_ids():
    """API для списка заданий с логами (только существующие, с именем HEVC-файла)."""
    from app.log_manager import log_manager

    jobs_storage = get_jobs_storage()
    items = []
    for job_id in log_manager.get_all_job_ids():
        job = jobs_storage.get(job_id)
        if not job:
            continue
        items.append({
            'id': job_id,
            'label': job.get_output_filename(),
            'created_at': job.created_at.isoformat() if job.created_at else None,
        })

    items.sort(key=lambda item: item.get('created_at') or '', reverse=True)
    for item in items:
        item.pop('created_at', None)

    return jsonify(items)


@main_bp.route('/api/logs/clear', methods=['POST'])
def api_logs_clear():
    """Очищает системный поток или логи конкретного задания."""
    from app.log_manager import log_manager

    job_id = request.args.get('job_id')
    stream = request.args.get('stream', 'system')

    if job_id:
        log_manager.clear_job_logs(job_id)
    elif stream == 'system':
        log_manager.clear_system_logs()
    else:
        return jsonify({'error': 'Укажите stream=system или job_id'}), 400

    return jsonify({'message': 'Логи очищены'})


def _cpu_model_from_proc_cpuinfo():
    """Читает модель CPU из /proc/cpuinfo (Linux, в т.ч. Docker)."""
    try:
        with open('/proc/cpuinfo', 'r', encoding='utf-8', errors='replace') as f:
            lines = f.readlines()
    except OSError:
        return None

    fields = {}
    for line in lines:
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        key = key.strip().lower()
        value = value.strip()
        if value and key not in fields:
            fields[key] = value

    # x86: model name; ARM: Hardware / Model / cpu model
    for key in ('model name', 'hardware', 'cpu model', 'model', 'processor'):
        value = fields.get(key)
        if not value:
            continue
        # В cpuinfo "processor : 0" — номер ядра, не модель
        if key in ('processor', 'model') and value.isdigit():
            continue
        return value
    return None


def _cpu_model_from_lscpu():
    """Пытается получить Model name через lscpu (если есть в образе)."""
    import subprocess
    try:
        result = subprocess.run(
            ['lscpu'],
            capture_output=True, text=True, timeout=3, check=False
        )
        if result.returncode != 0 or not result.stdout:
            return None
        for line in result.stdout.splitlines():
            if ':' not in line:
                continue
            key, value = line.split(':', 1)
            if key.strip().lower() in ('model name', 'model'):
                value = value.strip()
                if value and not value.isdigit():
                    return value
    except (OSError, FileNotFoundError, subprocess.TimeoutExpired):
        return None
    return None


def _cpu_model_from_windows():
    """Модель CPU на Windows через реестр (надёжнее WMIC)."""
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'HARDWARE\DESCRIPTION\System\CentralProcessor\0'
        ) as key:
            name, _ = winreg.QueryValueEx(key, 'ProcessorNameString')
            name = (name or '').strip()
            if name:
                return name
    except OSError:
        pass
    return None


def get_cpu_model():
    """Определяет модель CPU с несколькими источниками (Docker/Linux/Windows)."""
    import platform

    for getter in (
        _cpu_model_from_proc_cpuinfo,
        _cpu_model_from_lscpu,
        _cpu_model_from_windows if platform.system() == 'Windows' else None,
    ):
        if getter is None:
            continue
        model = getter()
        if model:
            return model

    # platform.processor() на Linux часто пустой; на Windows — семейство CPUID
    model = (platform.processor() or '').strip()
    if model:
        return model

    return 'Неизвестно'


@main_bp.route('/api/system-info')
def system_info():
    """Возвращает информацию о системе"""
    try:
        import multiprocessing
        import platform
        import psutil
        import sys
        import time
        from flask import __version__ as flask_version
        
        # Информация о процессоре
        cpu_count = multiprocessing.cpu_count()
        cpu_arch = platform.machine()
        cpu_model = get_cpu_model()
        
        # Информация о памяти
        memory = psutil.virtual_memory()
        
        # Информация о приложении
        app_version = "0.9.1"
        environment = os.getenv('FLASK_ENV', 'development')
        
        # Читаем номер сборки из файла
        try:
            with open('/app/build_number.txt', 'r', encoding='utf-8') as f:
                build_number = f.read().strip()
        except Exception:
            try:
                with open('build_number.txt', 'r', encoding='utf-8') as f:
                    build_number = f.read().strip()
            except Exception:
                build_number = '1'
        
        # Информация о системе
        os_info = f"{platform.system()} {platform.release()}"
        platform_name = platform.platform()
        
        # Время работы системы
        boot_time = psutil.boot_time()
        uptime_seconds = time.time() - boot_time
        uptime = format_uptime(uptime_seconds)
        
        # Статус компонентов
        system_status = 'ok'
        ffmpeg_status = 'ok' if check_ffmpeg() else 'error'
        ffmpeg_build = get_ffmpeg_build_info() if ffmpeg_status == 'ok' else {}
        ffmpeg_version = (
            get_ffmpeg_version_token() if ffmpeg_status == 'ok' else None
        )
        ffmpeg_version_label = (
            get_ffmpeg_version_label() if ffmpeg_status == 'ok' else None
        )
        mkvmerge_status, mkvmerge_version = get_mkvmerge_status()
        libfdk = has_libfdk_aac() if ffmpeg_status == 'ok' else False
        worker_status = 'ok'
        websocket_status = 'ok'
        
        return jsonify({
            'cpu_count': cpu_count,
            'cpu_arch': cpu_arch,
            'cpu_model': cpu_model,
            'memory': {
                'total': memory.total,
                'available': memory.available,
                'percent': memory.percent
            },
            'app_version': app_version,
            'environment': environment,
            'build_number': build_number,
            'python_version': f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            'flask_version': flask_version,
            'os_info': os_info,
            'platform': platform_name,
            'uptime': uptime,
            'system_status': system_status,
            'ffmpeg_status': ffmpeg_status,
            'ffmpeg_version': ffmpeg_version,
            'ffmpeg_version_label': ffmpeg_version_label,
            'ffmpeg_configuration': ffmpeg_build.get('configuration'),
            'ffmpeg_libraries': ffmpeg_build.get('libraries') or {},
            'libfdk_aac': libfdk,
            'mkvmerge_status': mkvmerge_status,
            'mkvmerge_version': mkvmerge_version,
            'worker_status': worker_status,
            'websocket_status': websocket_status,
            'status': 'ok'
        })
    except Exception as e:
        return jsonify({'error': f'Ошибка получения информации о системе: {str(e)}'}), 500

def format_uptime(seconds):
    """Форматирует время работы в читаемый вид"""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    
    if days > 0:
        return f"{days}д {hours}ч {minutes}м"
    elif hours > 0:
        return f"{hours}ч {minutes}м"
    else:
        return f"{minutes}м"

def get_ffmpeg_version():
    """Короткий токен версии FFmpeg (без префикса «ffmpeg »)."""
    try:
        return get_ffmpeg_version_token()
    except Exception as e:
        logger.main_logger.error(f"Ошибка получения версии FFmpeg: {e}")
        return None

def get_mkvmerge_status():
    """Возвращает (status, version_string) для mkvmerge."""
    import subprocess
    try:
        binary = get_mkvmerge_bin()
        result = subprocess.run(
            [binary, '--version'],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode != 0:
            return 'error', None
        first = (result.stdout or result.stderr or '').splitlines()
        version = first[0].strip() if first else binary
        return 'ok', version
    except Exception as e:
        logger.main_logger.warning(f"mkvmerge недоступен: {e}")
        return 'error', None

def check_ffmpeg():
    """Проверяет доступность FFmpeg"""
    try:
        import subprocess
        result = subprocess.run([get_ffmpeg_bin(), '-version'],
                              capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False
