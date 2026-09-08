from dataclasses import dataclass
from enum import Enum
from datetime import datetime, timezone
import hashlib
import os
from .presets import (
    DEFAULT_AUDIO_ID,
    DEFAULT_VIDEO_ID,
    display_label,
    normalize_audio_downmix,
    preset_manager,
)


def utc_now() -> datetime:
    """Текущее время в UTC (timezone-aware)."""
    return datetime.now(timezone.utc)


def _iso_utc(dt: datetime | None) -> str | None:
    """ISO-8601 в UTC для API/UI (браузер форматирует в локальный TZ)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat().replace('+00:00', 'Z')

class JobStatus(Enum):
    PLANNED = "planned"
    IN_PROGRESS = "in_progress"
    DONE = "done"
    ERROR = "error"
    CANCELLED = "cancelled"

@dataclass
class Job:
    id: str
    filename: str
    original_path: str
    output_path: str | None = None
    status: JobStatus = JobStatus.PLANNED
    progress: float = 0.0
    error_message: str | None = None
    created_at: datetime = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    file_hash: str | None = None
    ffmpeg_params: dict = None
    total_frames: int | None = None
    video_id: str = DEFAULT_VIDEO_ID
    audio_id: str = DEFAULT_AUDIO_ID
    audio_downmix: str = "none"
    
    def __post_init__(self):
        if self.created_at is None:
            self.created_at = utc_now()
        if not self.video_id:
            self.video_id = DEFAULT_VIDEO_ID
        if not self.audio_id:
            self.audio_id = DEFAULT_AUDIO_ID
        try:
            self.audio_downmix = normalize_audio_downmix(self.audio_downmix)
        except ValueError:
            self.audio_downmix = "none"
        if self.ffmpeg_params is None:
            try:
                self.ffmpeg_params = preset_manager.resolve(
                    self.video_id, self.audio_id, self.audio_downmix
                )
            except ValueError:
                # Не оставляем label=stereo при encode=none
                self.ffmpeg_params = self.get_default_params()
                self.audio_downmix = normalize_audio_downmix(
                    self.ffmpeg_params.get('audio_downmix', 'none')
                )
        # job.audio_downmix — канон для label и encode
        self.ffmpeg_params['audio_downmix'] = self.audio_downmix
    
    @staticmethod
    def get_default_params():
        return {
            'video_map': '0:v:0',
            'audio_map': '0:a?',
            'subtitle_map': '0:s?',
            'data_map': '0:t?',
            'video_codec': 'libx265',
            'subtitle_codec': 'copy',
            'data_codec': 'copy',
            'preset': 'slow',
            'crf': '23',
            'pixel_format': 'yuv420p10le',
            'x265_params': 'level=5.1:ref=6',
            'tune': 'animation',
            'audio_codec': 'copy',
            'output_format': 'matroska',
            'audio_downmix': 'none',
            'cpu_threads': '0',
            'thread_queue_size': '0',
            'x265_pools': '0',
            'update_metadata': 'true',
            'metadata_nickname': '',
            'metadata_video_title': '',
        }
    
    def calculate_hash(self):
        """Вычисляет SHA256 хеш файла"""
        if not os.path.exists(self.original_path):
            return None
        
        hash_sha256 = hashlib.sha256()
        with open(self.original_path, "rb") as f:
            for chunk in iter(lambda: f.read(4096), b""):
                hash_sha256.update(chunk)
        return hash_sha256.hexdigest()
    
    def verify_hash(self):
        """Проверяет целостность файла по хешу"""
        if self.file_hash is None:
            return False
        current_hash = self.calculate_hash()
        return current_hash == self.file_hash
    
    def get_output_filename(self):
        """Генерирует имя выходного файла"""
        name, ext = os.path.splitext(self.filename)
        return f"{name}_[HEVC]_{self.id[:8]}.mkv"

    def display_label(self) -> str:
        """Подпись пресета для UI: video / audio (+ downmix)."""
        video = preset_manager.get_video(self.video_id)
        audio = preset_manager.get_audio(self.audio_id)
        return display_label(
            video,
            audio,
            self.audio_downmix,
            video_id=self.video_id,
            audio_id=self.audio_id,
        )
    
    def to_dict(self):
        """Преобразует объект в словарь для JSON сериализации"""
        # Убеждаемся, что все значения в ffmpeg_params являются строками
        safe_ffmpeg_params = {}
        if self.ffmpeg_params:
            for key, value in self.ffmpeg_params.items():
                if isinstance(value, (int, float)):
                    safe_ffmpeg_params[key] = str(value)
                else:
                    safe_ffmpeg_params[key] = value
        
        return {
            'id': self.id,
            'filename': self.filename,
            'output_filename': self.get_output_filename(),
            'status': self.status.value,
            'progress': self.progress,
            'error_message': self.error_message,
            'created_at': _iso_utc(self.created_at),
            'started_at': _iso_utc(self.started_at),
            'completed_at': _iso_utc(self.completed_at),
            'ffmpeg_params': safe_ffmpeg_params,
            'total_frames': self.total_frames,
            'video_id': self.video_id,
            'audio_id': self.audio_id,
            'audio_downmix': self.audio_downmix,
            'preset_label': self.display_label(),
        }
