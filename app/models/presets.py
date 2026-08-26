from dataclasses import dataclass
from typing import Dict, List, Optional
import os
import yaml


@dataclass
class AudioSettings:
    """Настройки аудио кодирования"""
    codec: str
    bitrate: Optional[str] = None
    sample_rate: Optional[str] = None
    vbr: Optional[bool] = None  # VBR для Opus
    
    def to_dict(self):
        return {
            'codec': self.codec,
            'bitrate': self.bitrate,
            'sample_rate': self.sample_rate,
            'vbr': self.vbr
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        return cls(
            codec=data.get('codec', 'copy'),
            bitrate=data.get('bitrate'),
            sample_rate=data.get('sample_rate'),
            vbr=data.get('vbr')
        )


@dataclass
class EncodingPreset:
    """Пресет кодирования"""
    id: str
    name: str
    description: str
    video_codec: str = "libx265"
    preset: str = "slow"
    crf: str = "23"
    pixel_format: str = "yuv420p10le"
    x265_params: str = "level=5.1:ref=6"
    tune: str = "animation"
    audio_settings: AudioSettings = None
    output_format: str = "matroska"
    video_map: str = "0:v:0"
    audio_map: str = "0:a?"
    subtitle_map: str = "0:s?"
    data_map: str = "0:t?"
    subtitle_codec: str = "copy"
    data_codec: str = "copy"
    
    def __post_init__(self):
        if self.audio_settings is None:
            self.audio_settings = AudioSettings(codec="copy")
    
    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description,
            'video_codec': self.video_codec,
            'preset': self.preset,
            'crf': self.crf,
            'pixel_format': self.pixel_format,
            'x265_params': self.x265_params,
            'tune': self.tune,
            'audio_settings': self.audio_settings.to_dict(),
            'output_format': self.output_format,
            'video_map': self.video_map,
            'audio_map': self.audio_map,
            'subtitle_map': self.subtitle_map,
            'data_map': self.data_map,
            'subtitle_codec': self.subtitle_codec,
            'data_codec': self.data_codec
        }
    
    @classmethod
    def from_dict(cls, data: dict):
        audio_settings = AudioSettings.from_dict(data.get('audio_settings', {}))
        return cls(
            id=data.get('id', ''),
            name=data.get('name', ''),
            description=data.get('description', ''),
            video_codec=data.get('video_codec', 'libx265'),
            preset=data.get('preset', 'slow'),
            crf=data.get('crf', '23'),
            pixel_format=data.get('pixel_format', 'yuv420p10le'),
            x265_params=data.get('x265_params', 'level=5.1:ref=6'),
            tune=data.get('tune', 'animation'),
            audio_settings=audio_settings,
            output_format=data.get('output_format', 'matroska'),
            video_map=data.get('video_map', '0:v:0'),
            audio_map=data.get('audio_map', '0:a?'),
            subtitle_map=data.get('subtitle_map', '0:s?'),
            data_map=data.get('data_map', '0:t?'),
            subtitle_codec=data.get('subtitle_codec', 'copy'),
            data_codec=data.get('data_codec', 'copy')
        )
    
    def get_ffmpeg_params(self):
        """Возвращает параметры для FFmpeg"""
        audio_codec = self.audio_settings.codec
        if audio_codec in (None, '', 'null', 'None'):
            audio_codec = None

        params = {
            'video_map': self.video_map,
            'audio_map': self.audio_map,
            'subtitle_map': self.subtitle_map,
            'data_map': self.data_map,
            'video_codec': self.video_codec,
            'subtitle_codec': self.subtitle_codec,
            'data_codec': self.data_codec,
            'preset': self.preset,
            'crf': self.crf,
            'pixel_format': self.pixel_format,
            'x265_params': self.x265_params,
            'tune': self.tune,
            'audio_codec': audio_codec,
            'output_format': self.output_format
        }
        
        if audio_codec and audio_codec != 'copy':
            if self.audio_settings.bitrate:
                params['audio_bitrate'] = self.audio_settings.bitrate
            if self.audio_settings.sample_rate:
                params['audio_sample_rate'] = self.audio_settings.sample_rate
            if self.audio_settings.vbr is not None:
                params['audio_vbr'] = str(self.audio_settings.vbr).lower()
        
        return params


class PresetManager:
    """Менеджер пресетов кодирования"""
    
    def __init__(self, presets_dir: str = None):
        if presets_dir is None:
            # Определяем путь к пресетам относительно корня проекта
            import os
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.dirname(os.path.dirname(current_dir))
            self.presets_dir = os.path.join(project_root, "presets")
            
            # Если пресеты не найдены в корне проекта, пробуем найти их в текущей директории
            if not os.path.exists(self.presets_dir):
                # Пробуем найти пресеты в текущей директории
                current_presets_dir = os.path.join(os.getcwd(), "presets")
                if os.path.exists(current_presets_dir):
                    self.presets_dir = current_presets_dir
                    print(f"Найдены пресеты в текущей директории: {self.presets_dir}")
                else:
                    # Пробуем найти пресеты относительно app директории
                    app_presets_dir = os.path.join(current_dir, "..", "..", "presets")
                    if os.path.exists(app_presets_dir):
                        self.presets_dir = os.path.abspath(app_presets_dir)
                        print(f"Найдены пресеты относительно app: {self.presets_dir}")
        else:
            self.presets_dir = presets_dir
        self.presets: Dict[str, EncodingPreset] = {}
        self.load_presets()
    
    def load_presets(self):
        """Загружает пресеты из YAML файлов"""
        if not os.path.exists(self.presets_dir):
            self.create_default_presets()
            return
        
        try:
            # Загружаем все YAML файлы из директории пресетов
            files_in_dir = os.listdir(self.presets_dir)
            
            for filename in files_in_dir:
                if filename.endswith('.yaml') or filename.endswith('.yml'):
                    preset_path = os.path.join(self.presets_dir, filename)
                    try:
                        with open(preset_path, 'r', encoding='utf-8') as f:
                            preset_data = yaml.safe_load(f)
                            if preset_data and 'id' in preset_data and 'name' in preset_data:
                                preset = EncodingPreset.from_dict(preset_data)
                                self.presets[preset.id] = preset
                    except Exception as e:
                        print(f"Ошибка загрузки пресета {filename}: {e}")
            
            if not self.presets:
                self.create_default_presets()
                
        except Exception as e:
            print(f"Ошибка загрузки пресетов: {e}")
            self.create_default_presets()
    
    def save_preset(self, preset: EncodingPreset):
        """Сохраняет отдельный пресет в YAML файл"""
        try:
            preset_path = os.path.join(self.presets_dir, f"{preset.id}.yaml")
            with open(preset_path, 'w', encoding='utf-8') as f:
                yaml.dump(preset.to_dict(), f, default_flow_style=False,
                         allow_unicode=True, sort_keys=False, indent=2)
        except Exception as e:
            print(f"Ошибка сохранения пресета {preset.id}: {e}")
    
    def create_default_presets(self):
        """Создает пресеты по умолчанию"""
        # Создаем директорию если её нет
        os.makedirs(self.presets_dir, exist_ok=True)
        
        # Пресет по умолчанию (текущие настройки)
        default_preset = EncodingPreset(
            id="default",
            name="По умолчанию",
            description="Стандартные настройки для кодирования HEVC",
            video_codec="libx265",
            preset="slow",
            crf="23",
            pixel_format="yuv420p10le",
            x265_params="level=5.1:ref=6",
            tune="animation",
            audio_settings=AudioSettings(codec="copy"),
            output_format="matroska"
        )
        
        # Пресет с AAC аудио
        aac_preset = EncodingPreset(
            id="aac_audio",
            name="HEVC + AAC",
            description="HEVC видео с AAC аудио",
            video_codec="libx265",
            preset="slow",
            crf="23",
            pixel_format="yuv420p10le",
            x265_params="level=5.1:ref=6",
            tune="animation",
            audio_settings=AudioSettings(
                codec="aac",
                bitrate="128k",
                sample_rate="48000"
            ),
            output_format="matroska"
        )
        
        # Пресет с Opus аудио
        opus_preset = EncodingPreset(
            id="opus_audio",
            name="HEVC + Opus",
            description="HEVC видео с Opus аудио",
            video_codec="libx265",
            preset="slow",
            crf="23",
            pixel_format="yuv420p10le",
            x265_params="level=5.1:ref=6",
            tune="animation",
            audio_settings=AudioSettings(
                codec="libopus",
                bitrate="128k",
                sample_rate="48000"
            ),
            output_format="matroska"
        )
        
        self.presets = {
            "default": default_preset,
            "aac_audio": aac_preset,
            "opus_audio": opus_preset
        }
        
        # Сохраняем каждый пресет в отдельный файл
        for preset in self.presets.values():
            self.save_preset(preset)
    
    def get_preset(self, preset_id: str) -> Optional[EncodingPreset]:
        """Получает пресет по ID"""
        return self.presets.get(preset_id)
    
    def get_all_presets(self) -> List[EncodingPreset]:
        """Получает все пресеты"""
        # Сортируем пресеты: "По умолчанию" всегда первый
        presets_list = list(self.presets.values())
        presets_list.sort(key=lambda x: (x.id != 'default', x.name))
        
        return presets_list
    
    def reload_presets(self):
        """Перезагружает пресеты из файлов"""
        self.presets.clear()
        self.load_presets()
    
    def add_preset(self, preset: EncodingPreset):
        """Добавляет новый пресет"""
        self.presets[preset.id] = preset
        self.save_preset(preset)
    
    def update_preset(self, preset_id: str, preset: EncodingPreset):
        """Обновляет существующий пресет"""
        if preset_id in self.presets:
            self.presets[preset_id] = preset
            self.save_preset(preset)
    
    def delete_preset(self, preset_id: str):
        """Удаляет пресет"""
        if preset_id in self.presets and preset_id != "default":
            # Удаляем файл пресета
            preset_path = os.path.join(self.presets_dir, f"{preset_id}.yaml")
            try:
                if os.path.exists(preset_path):
                    os.remove(preset_path)
            except Exception as e:
                print(f"Ошибка удаления файла пресета {preset_id}: {e}")
            
            # Удаляем из памяти
            del self.presets[preset_id]


# Глобальный экземпляр менеджера пресетов
preset_manager = PresetManager()
