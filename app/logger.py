import logging
import os
from datetime import datetime
from typing import Optional

from app.log_manager import log_manager


class LogManagerHandler(logging.Handler):
    """Пишет записи logging в in-memory LogManager."""

    def emit(self, record: logging.LogRecord):
        try:
            message = record.getMessage()
            log_manager.add_log(message, record.levelname)
        except Exception:
            self.handleError(record)


class VideoEncoderLogger:
    """Логгер для системы кодирования видео"""
    
    def __init__(self, log_dir: str = "/app/logs"):
        self.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)
        self._setup_loggers()

    @staticmethod
    def format_ffmpeg_command(command: list) -> str:
        """Форматирует команду FFmpeg в одну читаемую строку."""
        return ' '.join(f'"{arg}"' if ' ' in arg else arg for arg in command)

    def _is_verbose_logging(self) -> bool:
        """True в режиме разработки или при LOG_LEVEL=DEBUG."""
        flask_env = os.getenv('FLASK_ENV', 'development').lower()
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        return flask_env in ('development', 'testing') or log_level == 'DEBUG'
    
    def _setup_loggers(self):
        """Настраивает логгеры"""
        log_level = os.getenv('LOG_LEVEL', 'INFO').upper()
        console_log_level = os.getenv('CONSOLE_LOG_LEVEL', 'INFO').upper()
        
        log_level_map = {
            'DEBUG': logging.DEBUG,
            'INFO': logging.INFO,
            'WARNING': logging.WARNING,
            'ERROR': logging.ERROR,
            'CRITICAL': logging.CRITICAL
        }
        
        file_level = log_level_map.get(log_level, logging.INFO)
        console_level = log_level_map.get(console_log_level, logging.INFO)
        memory_log_level_str = os.getenv('MEMORY_LOG_LEVEL', 'INFO').upper()
        memory_level = log_level_map.get(memory_log_level_str, logging.INFO)
        
        self.main_logger = logging.getLogger('video_encoder')
        self.main_logger.setLevel(file_level)
        
        self.upload_logger = logging.getLogger('upload')
        self.upload_logger.setLevel(file_level)
        
        self.ffmpeg_logger = logging.getLogger('ffmpeg')
        self.ffmpeg_logger.setLevel(file_level)
        
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        )

        memory_handler = LogManagerHandler()
        memory_handler.setLevel(memory_level)
        
        main_handler = logging.FileHandler(os.path.join(self.log_dir, 'main.log'))
        main_handler.setFormatter(formatter)
        self.main_logger.addHandler(main_handler)
        self.main_logger.addHandler(memory_handler)
        
        upload_handler = logging.FileHandler(os.path.join(self.log_dir, 'upload.log'))
        upload_handler.setFormatter(formatter)
        self.upload_logger.addHandler(upload_handler)
        self.upload_logger.addHandler(memory_handler)
        
        ffmpeg_handler = logging.FileHandler(os.path.join(self.log_dir, 'ffmpeg.log'))
        ffmpeg_handler.setFormatter(formatter)
        self.ffmpeg_logger.addHandler(ffmpeg_handler)
        
        console_handler = logging.StreamHandler()
        console_handler.setLevel(console_level)
        console_handler.setFormatter(formatter)
        self.main_logger.addHandler(console_handler)
    
    def log_upload_start(self, filename: str, file_size: int, job_id: str):
        """Логирует начало загрузки файла"""
        self.upload_logger.info(
            f"Начало загрузки файла: {filename} "
            f"(размер: {file_size} байт, задание: {job_id})"
        )
    
    def log_upload_progress(self, job_id: str, progress: float):
        """Логирует прогресс загрузки"""
        self.upload_logger.info(f"Прогресс загрузки {job_id}: {progress:.1f}%")
    
    def log_upload_complete(self, filename: str, job_id: str, file_hash: str):
        """Логирует завершение загрузки"""
        self.upload_logger.info(
            f"Загрузка завершена: {filename} "
            f"(задание: {job_id}, хеш: {file_hash[:16]}...)"
        )
    
    def log_upload_error(self, filename: str, error: str, job_id: Optional[str] = None):
        """Логирует ошибку загрузки"""
        job_info = f" (задание: {job_id})" if job_id else ""
        self.upload_logger.error(f"Ошибка загрузки {filename}{job_info}: {error}")
    
    def log_job_created(self, job_id: str, filename: str, status: str):
        """Логирует создание задания"""
        self.main_logger.info(f"Создано задание {job_id}: {filename} (статус: {status})")
    
    def log_job_status_change(self, job_id: str, old_status: str, new_status: str):
        """Логирует изменение статуса задания"""
        self.main_logger.info(f"Изменение статуса {job_id}: {old_status} -> {new_status}")
    
    def log_ffmpeg_start(self, job_id: str, command: list):
        """Логирует запуск FFmpeg с полной командой в main/job/ffmpeg логах."""
        readable_command = self.format_ffmpeg_command(command)
        verbose = self._is_verbose_logging()

        self.main_logger.info(f"Запуск FFmpeg для задания {job_id}")
        self.main_logger.info(f"Команда FFmpeg [{job_id}]: {readable_command}")

        self.ffmpeg_logger.info(f"Запуск FFmpeg для {job_id}: {readable_command}")
        if verbose:
            self.ffmpeg_logger.info(f"Детали команды FFmpeg для {job_id}:")
            for i, arg in enumerate(command):
                self.ffmpeg_logger.info(f"  [{i:2d}] {arg}")

        log_manager.add_job_log(job_id, f"Команда: {readable_command}")
        if verbose:
            log_manager.add_job_log(job_id, "Аргументы команды:")
            for i, arg in enumerate(command):
                log_manager.add_job_log(job_id, f"  [{i:2d}] {arg}")
    
    def log_ffmpeg_output(self, job_id: str, output: str):
        """Логирует вывод FFmpeg в файл (live — через log_callback)."""
        self.ffmpeg_logger.info(f"FFmpeg {job_id}: {output}")
    
    def log_ffmpeg_error(self, job_id: str, error: str):
        """Логирует ошибку FFmpeg"""
        self.ffmpeg_logger.error(f"Ошибка FFmpeg {job_id}: {error}")
        log_manager.add_job_log(job_id, f"Ошибка FFmpeg: {error}", 'ERROR')
    
    def log_ffmpeg_complete(self, job_id: str, return_code: int):
        """Логирует завершение FFmpeg"""
        status = "успешно" if return_code == 0 else f"с ошибкой (код: {return_code})"
        self.ffmpeg_logger.info(f"FFmpeg завершен {job_id}: {status}")
        log_manager.add_job_log(job_id, f"FFmpeg завершен: {status}")

# Глобальный экземпляр логгера
logger = VideoEncoderLogger()
