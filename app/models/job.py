from dataclasses import asdict, dataclass
from enum import Enum
from datetime import datetime, timezone
import os
import stat
from app.file_utils import calculate_file_hash
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
    input_path: str | None = None
    request_id: str | None = None
    
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
            # Без запасных хардкодов: только пресет video_id/audio_id
            self.ffmpeg_params = preset_manager.resolve(
                self.video_id, self.audio_id, self.audio_downmix
            )
        # job.audio_downmix — канон для label и encode
        self.ffmpeg_params['audio_downmix'] = self.audio_downmix
    
    def calculate_hash(self):
        """Вычисляет SHA256 хеш файла"""
        if not os.path.exists(self.original_path):
            return None
        
        return calculate_file_hash(self.original_path)
    
    def verify_hash(self):
        """Проверяет целостность файла по хешу"""
        if self.file_hash is None:
            return False
        current_hash = self.calculate_hash()
        return current_hash == self.file_hash
    
    def get_output_filename(self):
        """Рабочее имя на диске с ID задания для защиты от совпадений."""
        name, ext = os.path.splitext(self.filename)
        return f"{name}_[HEVC]_{self.id[:8]}.mkv"

    def get_download_filename(self):
        """Готовое имя для скачивания и сохранения вне рабочего каталога."""
        filename = os.path.basename(self.filename.replace('\\', '/'))
        name, _ = os.path.splitext(filename)
        return f"{name}_[HEVC].mkv"

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

    def available_output_size(self) -> int | None:
        """Размер доступного финального файла; видео не читается."""
        if self.status != JobStatus.DONE or not self.output_path:
            return None
        try:
            info = os.stat(self.output_path)
            if stat.S_ISREG(info.st_mode) and os.access(self.output_path, os.R_OK):
                return info.st_size
        except OSError:
            pass
        return None

    def to_record(self) -> dict:
        """Внутренняя запись для диска, включая рабочие пути и хеш."""
        record = asdict(self)
        record['status'] = self.status.value
        for key in ('created_at', 'started_at', 'completed_at'):
            record[key] = _iso_utc(getattr(self, key))
        return record

    @classmethod
    def from_record(cls, record: dict) -> 'Job':
        record = dict(record)
        record['status'] = JobStatus(record['status'])
        for key in ('created_at', 'started_at', 'completed_at'):
            value = record.get(key)
            record[key] = datetime.fromisoformat(value) if value else None
        return cls(**record)
    
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
        
        output_size = self.available_output_size()
        return {
            'id': self.id,
            'input_path': self.input_path,
            'request_id': self.request_id,
            'filename': self.filename,
            'output_filename': self.get_download_filename(),
            'output_available': output_size is not None,
            'download_url': f'/api/jobs/{self.id}/download' if output_size is not None else None,
            'output_size_bytes': output_size,
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
