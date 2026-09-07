import subprocess
import threading
import time
import os
import re
import copy
import json
from typing import Callable

from app.models.job import Job, JobStatus, utc_now
from app.file_utils import cleanup_file
from app.internal_source import (
    cleanup_job_original,
    is_internal_source_path,
    original_in_use_by_jobs,
)
from app.logger import logger
from app.ffmpeg_bootstrap import get_ffmpeg_bin, get_ffprobe_bin
from app.mkvmerge_remux import (
    build_mkvmerge_options,
    cleanup_job_sidecars,
    encode_partial_path,
    fix_font_attachment_mime_types,
    get_mkvmerge_bin,
    identify_encoded_file,
    is_update_metadata_enabled,
    option_file_path,
    remux_temp_path,
    write_option_file,
)

VALID_X265_TUNES = frozenset({
    'psnr', 'ssim', 'grain', 'animation',
    'fastdecode', 'fast-decode', 'zerolatency', 'zero-latency',
})


class FFmpegWorker:
    def __init__(self, progress_callback: Callable | None = None, log_callback: Callable | None = None, status_callback: Callable | None = None):
        self.progress_callback = progress_callback
        self.log_callback = log_callback
        self.status_callback = status_callback
        self.running_jobs = {}
        self.job_queue = []
        self.worker_thread = None
        self.stop_event = threading.Event()
        self.processes = {}  # job_id -> Popen FFmpeg
        self._cancel_requested = set()  # job_id с запросом отмены
        self._missing_total_frames_logged = set()  # job_id, для которых уже писали warning
        self._lock = threading.Lock()
        
        # Инициализация завершена
        
        self.start_worker()
    
    def start_worker(self):
        """Запускает фоновый поток для обработки заданий"""
        if self.worker_thread is None or not self.worker_thread.is_alive():
            self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
            self.worker_thread.start()
    
    def _worker_loop(self):
        """Основной цикл воркера"""
        while not self.stop_event.is_set():
            job = None
            with self._lock:
                if self.job_queue:
                    job = self.job_queue.pop(0)
            if job:
                self._process_job(job)
            else:
                time.sleep(0.25)
    
    def add_job(self, job: Job):
        """Добавляет задание в очередь"""
        with self._lock:
            self._cancel_requested.discard(job.id)
            self.job_queue.append(job)
            queue_pos = len(self.job_queue)
        job.status = JobStatus.PLANNED
        logger.main_logger.info(f"Задание {job.id} добавлено в очередь FFmpeg (позиция: {queue_pos})")

    def _is_cancel_requested(self, job_id: str) -> bool:
        with self._lock:
            return job_id in self._cancel_requested

    def _mark_job_cancelled(self, job: Job, emit: bool = True):
        """Помечает задание как отменённое (без повторной эмиссии при необходимости)."""
        job.status = JobStatus.CANCELLED
        job.error_message = "Задание отменено пользователем"
        job.completed_at = utc_now()
        if emit and self.status_callback:
            self.status_callback(job.id, job.status, job.error_message)

    def _kill_process(self, process: subprocess.Popen, job_id: str) -> bool:
        """Завершает процесс FFmpeg (terminate → wait → kill) и проверяет, что он мёртв."""
        if process is None:
            return True
        if process.poll() is not None:
            logger.main_logger.info(f"FFmpeg для {job_id} уже завершён (код {process.returncode})")
            return True

        pid = process.pid
        logger.main_logger.info(f"Остановка FFmpeg PID={pid} для задания {job_id}")
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            logger.main_logger.warning(f"FFmpeg PID={pid} не завершился за 5с, kill()")
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception as e:
                logger.main_logger.error(f"Не удалось убить FFmpeg PID={pid}: {e}")

        dead = process.poll() is not None
        if dead:
            logger.main_logger.info(f"FFmpeg PID={pid} для {job_id} остановлен (код {process.returncode})")
        else:
            logger.main_logger.error(f"FFmpeg PID={pid} для {job_id} всё ещё жив после kill")
        return dead
    
    def _process_job(self, job: Job):
        """Обрабатывает одно задание"""
        try:
            # Гонка: задание сняли с очереди / отменили между pop и стартом
            if self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                logger.main_logger.info(f"Задание {job.id} отменено до старта — пропуск")
                self._mark_job_cancelled(job)
                return

            logger.main_logger.info(f"Начало обработки задания {job.id}: {job.filename}")
            
            job.status = JobStatus.IN_PROGRESS
            job.started_at = utc_now()
            if self.status_callback:
                self.status_callback(job.id, job.status, job.error_message)

            if self._is_cancel_requested(job.id):
                logger.main_logger.info(f"Задание {job.id} отменено сразу после старта статуса — пропуск FFmpeg")
                self._mark_job_cancelled(job)
                return
            
            # Генерируем имя выходного файла
            output_filename = job.get_output_filename()
            output_path = os.path.join('/app/outputs', output_filename)
            job.output_path = output_path
            
            logger.main_logger.info(f"Выходной файл: {output_path}")

            self._ensure_mkvmerge_preflight(job)
            
            # Получаем общее количество кадров из вывода FFmpeg (будет установлено при первом парсинге)
            job.total_frames = 0  # Будет установлено при парсинге NUMBER_OF_FRAMES
            
            # Проверка целостности файла перед кодированием
            if job.file_hash and not job.verify_hash():
                raise ValueError('Проверка целостности файла не пройдена')

            self._warn_multiple_audio_tracks(job)

            if self._is_cancel_requested(job.id):
                logger.main_logger.info(f"Задание {job.id} отменено перед запуском FFmpeg — пропуск")
                self._mark_job_cancelled(job)
                return
            
            encode_path = self._encode_output_path(job)
            cmd = self._build_ffmpeg_command(job, encode_path)
            
            # Проверяем, что все элементы команды являются строками
            for i, item in enumerate(cmd):
                if not isinstance(item, str):
                    logger.main_logger.error(f"Элемент {i} команды не является строкой: {item} (тип: {type(item)})")
                    raise ValueError(f"Элемент {i} команды не является строкой: {item} (тип: {type(item)})")
            
            # Логируем полную команду FFmpeg (main.log, ffmpeg.log, job log stream)
            logger.log_ffmpeg_start(job.id, cmd)
            
            # Запускаем FFmpeg
            with self._lock:
                self.running_jobs[job.id] = job
            self._run_ffmpeg(job, cmd, encode_path)
            
        except Exception as e:
            if self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                self._mark_job_cancelled(job)
            else:
                job.status = JobStatus.ERROR
                job.error_message = str(e)
                job.completed_at = utc_now()
                if self.status_callback:
                    self.status_callback(job.id, job.status, job.error_message)
                if self.log_callback:
                    self.log_callback(job.id, f"Ошибка: {str(e)}")
        finally:
            with self._lock:
                self.running_jobs.pop(job.id, None)
                self.processes.pop(job.id, None)
                self._cancel_requested.discard(job.id)
                self._missing_total_frames_logged.discard(job.id)
            if job.status in (JobStatus.CANCELLED, JobStatus.ERROR):
                self._cleanup_unsuccessful_job(job)
    
    def _warn_multiple_audio_tracks(self, job: Job):
        """Предупреждает, если в исходнике несколько аудиодорожек."""
        params = job.ffmpeg_params or {}
        audio_codec = params.get('audio_codec')
        audio_map = params.get('audio_map')

        if audio_codec in ['remove', None, 'None', '', 'null'] or \
           audio_map in [None, 'None', '', 'null']:
            return

        try:
            result = subprocess.run(
                [
                    get_ffprobe_bin(), '-v', 'error',
                    '-select_streams', 'a',
                    '-show_entries', 'stream=index',
                    '-of', 'csv=p=0',
                    job.original_path,
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode != 0:
                return

            tracks = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            if len(tracks) > 1:
                warning = (
                    f"⚠️ Обнаружено {len(tracks)} аудиодорожек "
                    f"(audio_map={audio_map})"
                )
                logger.main_logger.warning(warning)
                if self.log_callback:
                    self.log_callback(job.id, warning)
        except Exception as e:
            logger.main_logger.debug(f"ffprobe: не удалось проверить аудиодорожки для {job.id}: {e}")

    def _should_remux_metadata(self, job: Job) -> bool:
        return is_update_metadata_enabled(job.ffmpeg_params or {})

    def _ensure_mkvmerge_preflight(self, job: Job) -> None:
        """До encode: без mkvmerge remux невозможен — сразу ERROR."""
        if not self._should_remux_metadata(job):
            return
        get_mkvmerge_bin()

    def _cleanup_job_sidecars(self, job: Job) -> None:
        if not job.output_path:
            return
        cleanup_job_sidecars(job.output_path, job.id)

    def _active_jobs(self) -> list:
        """Задания в очереди и в работе (для проверки общих исходников)."""
        with self._lock:
            return list(self.job_queue) + list(self.running_jobs.values())

    def _cleanup_temp_original(self, job: Job) -> None:
        """Удаляет upload/temp исходник; internal/read-only не трогает."""
        path = job.original_path
        if not path:
            return
        if is_internal_source_path(path):
            logger.main_logger.info(f"Исходник сохранён (internal): {path}")
            return
        if original_in_use_by_jobs(path, self._active_jobs(), job.id):
            logger.main_logger.info(
                f"Исходник не удалён (используется другим заданием): {path}"
            )
            return
        existed = os.path.exists(path)
        cleanup_job_original(path)
        if existed:
            logger.main_logger.info(f"Исходный файл удален: {path}")

    def _cleanup_incomplete_output(self, job: Job) -> None:
        """Убирает битый/недописанный финальный .mkv после ERROR/cancel."""
        if not job.output_path:
            return
        if job.output_path == job.original_path:
            return
        if os.path.exists(job.output_path):
            cleanup_file(job.output_path)
            logger.main_logger.info(f"Неполный выходной файл удален: {job.output_path}")

    def _cleanup_unsuccessful_job(self, job: Job) -> None:
        """Sidecar + temp original + мусорный финальный mkv после ERROR/cancel."""
        self._cleanup_job_sidecars(job)
        self._cleanup_temp_original(job)
        self._cleanup_incomplete_output(job)

    def _encode_output_path(self, job: Job) -> str:
        """Путь encode: *.partial.mkv если дальше будет remux, иначе финальное имя."""
        if self._should_remux_metadata(job) and job.output_path:
            return encode_partial_path(job.output_path)
        return job.output_path

    def _build_ffmpeg_command(self, job: Job, output_path: str | None = None) -> list:
        """Строит команду FFmpeg на основе параметров задания (без тегов/метаинфы)."""
        params = copy.deepcopy(job.ffmpeg_params)
        dest_path = output_path if output_path is not None else self._encode_output_path(job)
        
        # Убеждаемся, что все параметры являются строками
        for key, value in params.items():
            if not isinstance(value, str):
                params[key] = str(value)
        
        # Определяем количество потоков (0 = авто, FFmpeg сам выберет)
        cpu_threads_input = int(params.get('cpu_threads', 0))
        cpu_threads = min(cpu_threads_input, 64) if cpu_threads_input > 0 else 0
        
        # Размер очереди потоков (0 = авто, FFmpeg сам выберет)
        thread_queue_size_input = int(params.get('thread_queue_size', 0))
        thread_queue_size = min(thread_queue_size_input, 8192) if thread_queue_size_input > 0 else 0
        
        # Пул потоков x265 (0 = авто, x265 сам выберет)
        x265_pools_input = int(params.get('x265_pools', 0))
        x265_pools = min(x265_pools_input, 32) if x265_pools_input > 0 else 0
        
        # Логируем настройки
        if cpu_threads_input == 0:
            logger.main_logger.info("Потоки CPU: автоматически (FFmpeg выберет сам, -threads не передаётся)")
        else:
            logger.main_logger.info(f"Используем {cpu_threads} потоков CPU для кодирования (libx265 frameNumThreads)")
        
        # Строим x265 параметры с учетом настроек производительности
        x265_params = params['x265_params']
        
        # Добавляем pools/frame-threads только при явном указании x265_pools > 0
        if x265_pools_input > 0 and 'pools=' not in x265_params:
            max_frame_threads = min(x265_pools, 16)
            x265_params += f":pools={x265_pools}:frame-threads={max_frame_threads}"
            logger.main_logger.info(f"x265 pools: {x265_pools}, frame-threads: {max_frame_threads}")
        elif x265_pools_input > 0 and 'pools=' in x265_params and 'frame-threads=' not in x265_params:
            max_frame_threads = min(x265_pools, 16)
            x265_params += f":frame-threads={max_frame_threads}"
            logger.main_logger.info(f"x265 frame-threads: {max_frame_threads}")
        elif x265_pools_input == 0:
            logger.main_logger.info("x265 pools: автоматическое определение (pools/frame-threads не передаются)")

        if thread_queue_size_input == 0:
            logger.main_logger.info("thread_queue_size: автоматически (параметр не передаётся)")
        else:
            logger.main_logger.info(f"thread_queue_size: {thread_queue_size}")
        
        # Начинаем формировать команду
        cmd = [get_ffmpeg_bin()]
        if thread_queue_size_input > 0:
            cmd.extend(['-thread_queue_size', str(thread_queue_size)])
        cmd.extend(['-i', job.original_path])
        
        # Добавляем карту видео (всегда обязательна)
        cmd.extend(['-map', str(params['video_map'])])
        
        # Обрабатываем аудио дорожки
        audio_codec = params.get('audio_codec')
        audio_map = params.get('audio_map')
        
        if audio_codec in ['remove', None, 'None', '', 'null'] or \
           audio_map in [None, 'None', '', 'null']:
            # Удаляем все аудио дорожки
            cmd.append('-an')
        else:
            # Добавляем карту аудио
            cmd.extend(['-map', str(audio_map)])
            # Добавляем кодек аудио
            cmd.extend(['-c:a', str(audio_codec)])
            
            # Добавляем дополнительные параметры аудио если кодек не copy
            if audio_codec != 'copy':
                if 'audio_bitrate' in params and params['audio_bitrate']:
                    cmd.extend(['-b:a', str(params['audio_bitrate'])])
                
                if 'audio_sample_rate' in params and params['audio_sample_rate']:
                    cmd.extend(['-ar', str(params['audio_sample_rate'])])
                
                # Добавляем VBR для Opus
                if audio_codec == 'libopus':
                    vbr_value = params.get('audio_vbr', 'true')
                    if isinstance(vbr_value, str) and vbr_value.lower() in ['false', '0', 'no', 'off']:
                        cmd.extend(['-vbr', 'off'])
                    else:
                        cmd.extend(['-vbr', 'on'])
        
        # Обрабатываем субтитры
        subtitle_codec = params.get('subtitle_codec')
        subtitle_map = params.get('subtitle_map')
        
        if subtitle_codec in ['remove', None, 'None', '', 'null'] or \
           subtitle_map in [None, 'None', '', 'null']:
            # Удаляем все субтитры
            cmd.append('-sn')
        else:
            # Добавляем карту субтитров
            cmd.extend(['-map', str(subtitle_map)])
            # Добавляем кодек субтитров
            cmd.extend(['-c:s', str(subtitle_codec)])
        
        # Обрабатываем данные
        data_codec = params.get('data_codec')
        data_map = params.get('data_map')
        
        if data_codec in ['remove', None, 'None', '', 'null'] or \
           data_map in [None, 'None', '', 'null']:
            # Удаляем все дополнительные данные
            cmd.append('-dn')
        else:
            # Добавляем карту данных
            cmd.extend(['-map', str(data_map)])
            # Добавляем кодек данных
            cmd.extend(['-c:t', str(data_codec)])
        
        # Добавляем кодек видео и параметры кодирования
        cmd.extend([
            '-c:v', str(params['video_codec']),
            '-preset', str(params['preset']),
            '-crf', str(params['crf']),
            '-pix_fmt', str(params['pixel_format']),
            '-x265-params', str(x265_params),
        ])

        tune = str(params.get('tune', '')).strip()
        if tune in VALID_X265_TUNES:
            cmd.extend(['-tune', tune])
        elif tune.lower() not in ('none', '', 'null'):
            logger.main_logger.warning(
                f"Неизвестный tune x265: {tune!r}, параметр -tune не передаётся"
            )
        
        # -threads применяется libx265 (frameNumThreads); -thread_type — опция x264, для libx265 не используется
        if cpu_threads_input > 0:
            cmd.extend(['-threads', str(cpu_threads)])

        cmd.extend([
            '-f', str(params['output_format']),
            '-y',  # Перезаписывать выходной файл
            str(dest_path)
        ])
        
        return cmd

    def _fix_font_mimes_before_merge(self, job: Job, encode_path: str) -> None:
        """Перед remux: выравнивает MIME шрифтов по расширению."""
        if not self._should_remux_metadata(job):
            return
        if not encode_path or not os.path.exists(encode_path):
            return
        fixes = fix_font_attachment_mime_types(encode_path)
        if not fixes:
            return
        summary = ', '.join(
            f"{item['file_name'] or item['id']}: "
            f"{item['current_mime'] or '(пусто)'} → {item['expected_mime']}"
            for item in fixes
        )
        message = f'Исправлены MIME шрифтов ({len(fixes)}): {summary}'
        logger.main_logger.info(f'{message} [{job.id}]')
        if self.log_callback:
            self.log_callback(job.id, message)

    def _finalize_encoded_output(self, job: Job, encode_path: str) -> None:
        """Правка MIME шрифтов (если remux), затем remux или подмена partial."""
        self._fix_font_mimes_before_merge(job, encode_path)
        if self._should_remux_metadata(job):
            self._run_mkvmerge_remux(job, encode_path)
            return
        if encode_path and job.output_path and encode_path != job.output_path:
            os.replace(encode_path, job.output_path)

    def _run_mkvmerge_remux(self, job: Job, encode_path: str) -> None:
        """Собирает JSON-опции и запускает mkvmerge @json во временный файл."""
        if not encode_path or not os.path.exists(encode_path):
            raise RuntimeError(f'Remux: закодированный файл не найден: {encode_path}')
        if not job.output_path:
            raise RuntimeError('Remux: не задан выходной путь')

        logger.main_logger.info(f'Remux: identify {encode_path}')
        identify = identify_encoded_file(encode_path)
        tmp_output = remux_temp_path(job.output_path)
        json_path = option_file_path(job.output_path, job.id)
        options = build_mkvmerge_options(
            encode_path,
            tmp_output,
            identify,
            job.ffmpeg_params or {},
        )
        write_option_file(json_path, options)
        logger.main_logger.info(f'Remux: JSON опций записан {json_path}')
        logger.main_logger.info(
            f'Опции mkvmerge [{job.id}]: {json.dumps(options, ensure_ascii=False)}'
        )
        if self.log_callback:
            self.log_callback(job.id, f'Remux: JSON {json_path}')

        cmd = [get_mkvmerge_bin(), f'@{json_path}']
        logger.main_logger.info(f'Запуск mkvmerge для задания {job.id}: {cmd[0]} @{json_path}')

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
        )
        with self._lock:
            self.processes[job.id] = process

        if self._is_cancel_requested(job.id) or self.stop_event.is_set():
            self._kill_process(process, job.id)
            self._mark_job_cancelled(job)
            raise RuntimeError('Задание отменено на шаге remux')

        for line in iter(process.stdout.readline, ''):
            if self.stop_event.is_set() or self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                self._kill_process(process, job.id)
                break
            line = line.strip()
            if line:
                logger.main_logger.info(f'mkvmerge {job.id}: {line}')
                if self.log_callback:
                    self.log_callback(job.id, line)

        return_code = process.wait()

        if self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
            self._mark_job_cancelled(job)
            cleanup_file(tmp_output)
            raise RuntimeError('Задание отменено на шаге remux')

        if return_code not in (0, 1):
            cleanup_file(tmp_output)
            raise RuntimeError(f'mkvmerge завершился с кодом {return_code}')
        if return_code == 1:
            logger.main_logger.warning(f'mkvmerge завершился с предупреждениями для {job.id}')
        if not os.path.exists(tmp_output):
            raise RuntimeError(f'Remux: mkvmerge не создал файл {tmp_output}')

        os.replace(tmp_output, job.output_path)
        cleanup_file(encode_path)
        cleanup_file(json_path)
        logger.main_logger.info(f'Remux завершён для {job.id}: {job.output_path}')

    def _run_ffmpeg(self, job: Job, cmd: list, encode_path: str | None = None):
        """Запускает FFmpeg и отслеживает прогресс"""
        process = None
        try:
            if self._is_cancel_requested(job.id):
                self._mark_job_cancelled(job)
                return

            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                universal_newlines=True,
                bufsize=1
            )
            with self._lock:
                self.processes[job.id] = process

            # Отмена могла прийти между проверкой и Popen
            if self._is_cancel_requested(job.id) or self.stop_event.is_set():
                self._kill_process(process, job.id)
                self._mark_job_cancelled(job)
                return
            
            # Читаем вывод FFmpeg построчно
            for line in iter(process.stdout.readline, ''):
                if self.stop_event.is_set() or self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                    self._kill_process(process, job.id)
                    break
                
                line = line.strip()
                if line:
                    logger.log_ffmpeg_output(job.id, line)
                    if self.log_callback:
                        self.log_callback(job.id, line)
                
                # Парсим прогресс из вывода FFmpeg
                progress = self._parse_progress(line, job.id)
                if progress is not None:
                    job.progress = progress
                    if self.progress_callback:
                        self.progress_callback(job.id, progress)
            
            # Ждем завершения процесса
            return_code = process.wait()
            logger.log_ffmpeg_complete(job.id, return_code)

            # Не перезаписывать CANCELLED кодом ошибки после kill
            if self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                self._mark_job_cancelled(job)
                if self.log_callback:
                    self.log_callback(job.id, "Задание остановлено, процесс FFmpeg завершён")
                return
            
            if return_code == 0:
                if self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                    self._mark_job_cancelled(job)
                    return

                self._finalize_encoded_output(job, encode_path or job.output_path)

                # Remux/replace уже записали финальный .mkv: отмена после этого
                # не должна прятать готовый файл (скачивание требует DONE).
                job.status = JobStatus.DONE
                job.progress = 100.0
                job.completed_at = utc_now()

                logger.main_logger.info(f"Задание {job.id} завершено успешно")
                
                if self.status_callback:
                    self.status_callback(job.id, job.status, job.error_message)
                
                # Удаляем временный исходник; internal/read-only не трогаем
                self._cleanup_temp_original(job)
            else:
                job.status = JobStatus.ERROR
                job.error_message = f"FFmpeg завершился с кодом {return_code}"
                job.completed_at = utc_now()
                
                logger.log_ffmpeg_error(job.id, f"Код возврата: {return_code}")
                
                if self.status_callback:
                    self.status_callback(job.id, job.status, job.error_message)
                
                if self.log_callback:
                    self.log_callback(job.id, f"Ошибка FFmpeg: код {return_code}")
        
        except Exception as e:
            if self._is_cancel_requested(job.id) or job.status == JobStatus.CANCELLED:
                if process is not None:
                    self._kill_process(process, job.id)
                self._mark_job_cancelled(job)
            else:
                job.status = JobStatus.ERROR
                job.error_message = str(e)
                job.completed_at = utc_now()
                
                if self.status_callback:
                    self.status_callback(job.id, job.status, job.error_message)
                
                if self.log_callback:
                    self.log_callback(job.id, f"Ошибка выполнения: {str(e)}")
    
    def _parse_progress(self, line: str, job_id: str) -> float | None:
        """Парсит прогресс из строки вывода FFmpeg"""
        try:
            # Логируем все строки FFmpeg для отладки
            logger.main_logger.debug(f"FFmpeg строка для {job_id}: {line}")
            
            # Парсим NUMBER_OF_FRAMES из вывода FFmpeg
            # Формат: [11:13:19] [885b3bae-5e5b-4011-81ee-cafc455375a3] NUMBER_OF_FRAMES-eng: 70317
            # Может быть несколько дорожек, берем только от первой видео-дорожки
            if 'NUMBER_OF_FRAMES' in line:
                frames_match = re.search(r'NUMBER_OF_FRAMES[^:]*:\s*(\d+)', line)
                if frames_match and job_id in self.running_jobs:
                    total_frames = int(frames_match.group(1))
                    job = self.running_jobs[job_id]
                    
                    # Проверяем, что это первая видео-дорожка
                    # Если total_frames уже установлен, не перезаписываем (берем только первое значение)
                    if not job.total_frames or job.total_frames == 0:
                        job.total_frames = total_frames
                        logger.main_logger.info(f"Установлено общее количество кадров для {job_id} (первая видео-дорожка): {total_frames}")
                        if self.log_callback:
                            self.log_callback(job_id, f"📊 Общее количество кадров: {total_frames}")
                    else:
                        logger.main_logger.debug(f"Пропускаем NUMBER_OF_FRAMES для {job_id}: уже установлено {job.total_frames} (возможно, это аудио-дорожка)")
                    return None  # Не возвращаем прогресс, только устанавливаем total_frames
            
            # Ищем строку с прогрессом (например: "frame= 1234 fps=25 q=28.0 size= 1024kB time=00:00:49.36 bitrate= 169.7kbits/s")
            # Также обрабатываем строки с time=N/A
            if 'frame=' in line and ('fps=' in line or 'time=' in line):
                # Извлекаем номер кадра
                frame_match = re.search(r'frame=\s*(\d+)', line)
                if frame_match:
                    current_frame = int(frame_match.group(1))
                    logger.main_logger.info(f"Найден кадр для {job_id}: {current_frame}")
                    
                    # Получаем задание по ID
                    if job_id in self.running_jobs:
                        job = self.running_jobs[job_id]
                        logger.main_logger.info(f"Задание {job_id}: total_frames={getattr(job, 'total_frames', 'None')}")
                        
                        if hasattr(job, 'total_frames') and job.total_frames and job.total_frames > 0:
                            progress = min((current_frame / job.total_frames) * 100, 100.0)
                            logger.main_logger.info(f"Прогресс {job_id}: {current_frame}/{job.total_frames} = {progress:.1f}%")
                            return progress
                        else:
                            if job_id not in self._missing_total_frames_logged:
                                self._missing_total_frames_logged.add(job_id)
                                logger.main_logger.warning(
                                    f"У задания {job_id} нет total_frames или оно равно 0"
                                )
                            
                            # Если нет total_frames, используем приблизительный расчет по времени
                            time_match = re.search(r'time=(\d+):(\d+):(\d+\.?\d*)', line)
                            if time_match:
                                hours = int(time_match.group(1))
                                minutes = int(time_match.group(2))
                                seconds = float(time_match.group(3))
                                current_time = hours * 3600 + minutes * 60 + seconds
                                
                                # Приблизительная оценка на основе времени (не очень точная)
                                # Предполагаем, что видео длится около 2 часов максимум
                                estimated_duration = 7200  # 2 часа в секундах
                                progress = min((current_time / estimated_duration) * 100, 100.0)
                                logger.main_logger.info(f"Приблизительный прогресс по времени для {job_id}: {progress:.1f}%")
                                return progress
                    
                    # Если не нашли задание с total_frames, используем приблизительный расчет
                    # FFmpeg обычно показывает прогресс в виде time=00:01:23.45
                    time_match = re.search(r'time=(\d+):(\d+):(\d+\.?\d*)', line)
                    if time_match:
                        hours = int(time_match.group(1))
                        minutes = int(time_match.group(2))
                        seconds = float(time_match.group(3))
                        current_time = hours * 3600 + minutes * 60 + seconds
                        
                        # Приблизительная оценка на основе времени (не очень точная)
                        # Предполагаем, что видео длится около 2 часов максимум
                        estimated_duration = 7200  # 2 часа в секундах
                        progress = min((current_time / estimated_duration) * 100, 100.0)
                        logger.main_logger.info(f"Приблизительный прогресс по времени для {job_id}: {progress:.1f}%")
                        return progress
                    elif 'time=N/A' in line:
                        # Если time=N/A, используем только номер кадра для приблизительной оценки
                        # Предполагаем, что видео имеет около 30 FPS и длится 2 часа максимум
                        estimated_fps = 30
                        estimated_duration = 7200  # 2 часа в секундах
                        estimated_total_frames = estimated_fps * estimated_duration
                        
                        progress = min((current_frame / estimated_total_frames) * 100, 100.0)
                        logger.main_logger.info(f"Приблизительный прогресс по кадрам для {job_id}: {current_frame}/{estimated_total_frames} = {progress:.1f}%")
                        return progress
                        
        except Exception as e:
            logger.main_logger.debug(f"Ошибка парсинга прогресса для {job_id}: {e}")
        
        return None
    
    def get_job_status(self, job_id: str) -> Job | None:
        """Возвращает статус задания"""
        with self._lock:
            return self.running_jobs.get(job_id)

    def remove_from_queue(self, job_id: str) -> bool:
        """Удаляет задание из очереди до старта. True если было в очереди."""
        with self._lock:
            before = len(self.job_queue)
            self.job_queue = [j for j in self.job_queue if j.id != job_id]
            self._cancel_requested.add(job_id)
            removed = len(self.job_queue) < before
        if removed:
            logger.main_logger.info(f"Задание {job_id} удалено из очереди FFmpeg")
        return removed
    
    def stop_job(self, job_id: str) -> dict:
        """
        Отменяет задание: снимает с очереди и/или убивает процесс FFmpeg.
        Безопасно при гонке «удалили, пока воркер уже взял задание».
        """
        removed_from_queue = False
        process = None
        job = None

        with self._lock:
            before = len(self.job_queue)
            self.job_queue = [j for j in self.job_queue if j.id != job_id]
            removed_from_queue = len(self.job_queue) < before
            self._cancel_requested.add(job_id)

            job = self.running_jobs.get(job_id)
            process = self.processes.get(job_id)

        if job is not None:
            self._mark_job_cancelled(job)

        process_killed = True
        if process is not None:
            process_killed = self._kill_process(process, job_id)
            with self._lock:
                if self.processes.get(job_id) is process:
                    del self.processes[job_id]

        logger.main_logger.info(
            f"stop_job {job_id}: queue_removed={removed_from_queue}, "
            f"had_process={process is not None}, process_dead={process_killed}"
        )
        return {
            'removed_from_queue': removed_from_queue,
            'had_process': process is not None,
            'process_dead': process_killed,
        }
    
    def stop_worker(self):
        """Останавливает воркер"""
        self.stop_event.set()
        
        with self._lock:
            processes = list(self.processes.items())
            self.processes.clear()

        for job_id, process in processes:
            self._kill_process(process, job_id)
        
        if self.worker_thread and self.worker_thread.is_alive():
            self.worker_thread.join(timeout=5)
