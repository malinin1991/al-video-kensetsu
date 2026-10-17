from __future__ import annotations

from dataclasses import dataclass
import os

import yaml

from app.audio_downmix import AudioDownmixError, parse_audio_map


VALID_AUDIO_DOWNMIX = frozenset({"none", "stereo"})
_SAFE_YAML_LOADER = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)


def _as_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    if text in ("true", "1", "yes", "on"):
        return True
    if text in ("false", "0", "no", "off", ""):
        return False
    return default


def _require_field(
    data: dict,
    key: str,
    *,
    allow_empty: bool = False,
    as_str: bool = False,
):
    """Обязательное поле YAML-пресета без подстановки дефолтов."""
    if key not in data:
        raise ValueError(f"В пресете отсутствует обязательное поле: {key}")
    value = data[key]
    if value is None:
        raise ValueError(f"В пресете поле {key} не может быть null")
    if as_str:
        value = str(value)
    if not allow_empty and isinstance(value, str) and value.strip() == "":
        raise ValueError(f"В пресете поле {key} пустое")
    return value


def _optional_str(data: dict, key: str, *, as_str: bool = False) -> str | None:
    """Опциональное поле: нет ключа / null / пусто → None (кодек/ffmpeg сами)."""
    if key not in data or data[key] is None:
        return None
    value = str(data[key]) if as_str or not isinstance(data[key], str) else data[key]
    if value.strip() == "":
        return None
    return value


@dataclass(frozen=True)
class VideoPreset:
    id: str
    name: str
    video_codec: str
    output_format: str
    description: str = ""
    preset: str | None = None
    crf: str | None = None
    pixel_format: str | None = None
    x265_params: str | None = None
    tune: str | None = None
    video_map: str | None = None
    is_default: bool = False

    def to_dict(self) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "default": self.is_default,
            "video_codec": self.video_codec,
            "output_format": self.output_format,
        }
        for key in (
            "preset",
            "crf",
            "pixel_format",
            "x265_params",
            "tune",
            "video_map",
        ):
            value = getattr(self, key)
            if value is not None:
                data[key] = value
        return data

    @classmethod
    def from_dict(cls, data: dict) -> VideoPreset:
        return cls(
            id=_require_field(data, "id"),
            name=_require_field(data, "name"),
            video_codec=_require_field(data, "video_codec"),
            output_format=_require_field(data, "output_format"),
            description=_optional_str(data, "description") or "",
            is_default=_as_bool(data.get("default"), False),
            preset=_optional_str(data, "preset", as_str=True),
            crf=_optional_str(data, "crf", as_str=True),
            pixel_format=_optional_str(data, "pixel_format"),
            x265_params=_optional_str(data, "x265_params", as_str=True),
            tune=_optional_str(data, "tune"),
            video_map=_optional_str(data, "video_map"),
        )


@dataclass(frozen=True)
class AudioPreset:
    id: str
    name: str
    description: str
    is_default: bool = False
    codec: str | None = "copy"
    bitrate: str | None = None
    sample_rate: str | None = None
    vbr: bool | None = None
    audio_map: str | None = "0:a?"

    def uses_fdk_aac(self) -> bool:
        return self.codec == "libfdk_aac"

    def is_reencode(self) -> bool:
        codec = self.codec
        if codec in (None, "", "null", "None", "remove"):
            return False
        if self.audio_map in (None, "", "null", "None"):
            return False
        return codec != "copy"

    def to_dict(self, available: bool | None = None) -> dict:
        data = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "default": self.is_default,
            "codec": self.codec,
            "bitrate": self.bitrate,
            "sample_rate": self.sample_rate,
            "vbr": self.vbr,
            "audio_map": self.audio_map,
        }
        if available is not None:
            data["available"] = available
        return data

    @classmethod
    def from_dict(cls, data: dict) -> AudioPreset:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            is_default=_as_bool(data.get("default"), False),
            codec=data.get("codec", "copy"),
            bitrate=data.get("bitrate"),
            sample_rate=(
                str(data["sample_rate"])
                if data.get("sample_rate") is not None
                else None
            ),
            vbr=data.get("vbr"),
            audio_map=data.get("audio_map", "0:a?"),
        )


@dataclass(frozen=True)
class SubtitlesPreset:
    id: str
    name: str
    description: str
    is_default: bool = False
    subtitle_map: str | None = "0:s?"
    subtitle_codec: str | None = "copy"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "default": self.is_default,
            "subtitle_map": self.subtitle_map,
            "subtitle_codec": self.subtitle_codec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> SubtitlesPreset:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            is_default=_as_bool(data.get("default"), False),
            subtitle_map=data.get("subtitle_map", "0:s?"),
            subtitle_codec=data.get("subtitle_codec", "copy"),
        )


@dataclass(frozen=True)
class DataPreset:
    id: str
    name: str
    description: str
    is_default: bool = False
    data_map: str | None = "0:t?"
    data_codec: str | None = "copy"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "default": self.is_default,
            "data_map": self.data_map,
            "data_codec": self.data_codec,
        }

    @classmethod
    def from_dict(cls, data: dict) -> DataPreset:
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            description=data.get("description", ""),
            is_default=_as_bool(data.get("default"), False),
            data_map=data.get("data_map", "0:t?"),
            data_codec=data.get("data_codec", "copy"),
        )


def normalize_audio_downmix(audio_downmix: str | None) -> str:
    if audio_downmix is None or str(audio_downmix).strip() == "":
        return "none"
    value = str(audio_downmix).strip().lower()
    if value in ("true", "1", "yes", "on"):
        value = "stereo"
    if value in ("false", "0", "no", "off"):
        value = "none"
    if value not in VALID_AUDIO_DOWNMIX:
        raise ValueError(f"Неизвестный audio_downmix: {audio_downmix}")
    return value


def _validate_stereo_audio_map(audio_map: str | None) -> None:
    """Те же правила, что parse_audio_map: только 0:a? / 0:a:N."""
    try:
        parse_audio_map(audio_map)
    except AudioDownmixError as exc:
        raise ValueError(str(exc)) from exc


def validate_audio_downmix_for_audio(
    audio: AudioPreset | None,
    audio_downmix: str,
) -> str:
    downmix = normalize_audio_downmix(audio_downmix)
    if downmix != "stereo":
        return downmix
    if audio is None:
        raise ValueError(
            "audio_downmix=stereo требует известный audio_id с перекодированием"
        )
    if not audio.is_reencode():
        codec = audio.codec
        if codec in (None, "", "null", "None", "remove") or audio.audio_map in (
            None,
            "",
            "null",
            "None",
        ):
            raise ValueError(
                "audio_downmix=stereo недоступен для audio_id=none (без аудио)"
            )
        if codec == "copy":
            raise ValueError(
                "audio_downmix=stereo недоступен при audio_id=copy "
                "(нужно перекодирование аудио)"
            )
        raise ValueError(
            "audio_downmix=stereo недоступен без перекодирования аудио"
        )
    _validate_stereo_audio_map(audio.audio_map)
    return downmix


def validate_audio_downmix_for_params(params: dict, audio_downmix: str) -> str:
    downmix = normalize_audio_downmix(audio_downmix)
    if downmix != "stereo":
        return downmix
    audio_codec = params.get("audio_codec")
    audio_map = params.get("audio_map")
    if (
        audio_codec in (None, "", "null", "None", "remove")
        or audio_map in (None, "", "null", "None")
    ):
        raise ValueError("audio_downmix=stereo недоступен без аудиодорожки")
    if audio_codec == "copy":
        raise ValueError(
            "audio_downmix=stereo недоступен при audio_codec=copy "
            "(нужно перекодирование аудио)"
        )
    _validate_stereo_audio_map(
        audio_map if isinstance(audio_map, str) else str(audio_map)
    )
    return downmix


def merge_ffmpeg_params(
    video: VideoPreset,
    audio: AudioPreset,
    subtitles: SubtitlesPreset,
    data: DataPreset,
    audio_downmix: str = "none",
) -> dict:
    downmix = validate_audio_downmix_for_audio(audio, audio_downmix)

    audio_codec = audio.codec
    if audio_codec in (None, "", "null", "None"):
        audio_codec = None

    params = {
        "video_map": video.video_map,
        "audio_map": audio.audio_map,
        "subtitle_map": subtitles.subtitle_map,
        "data_map": data.data_map,
        "video_codec": video.video_codec,
        "subtitle_codec": subtitles.subtitle_codec,
        "data_codec": data.data_codec,
        "preset": video.preset,
        "crf": video.crf,
        "pixel_format": video.pixel_format,
        "x265_params": video.x265_params,
        "tune": video.tune,
        "audio_codec": audio_codec,
        "output_format": video.output_format,
        "audio_downmix": downmix,
    }

    if audio_codec and audio_codec != "copy":
        if audio.bitrate:
            params["audio_bitrate"] = audio.bitrate
        if audio.sample_rate:
            params["audio_sample_rate"] = audio.sample_rate
        if audio.vbr is not None:
            params["audio_vbr"] = str(audio.vbr).lower()

    return params


def display_label(
    video: VideoPreset | None,
    audio: AudioPreset | None,
    audio_downmix: str = "none",
    video_id: str | None = None,
    audio_id: str | None = None,
) -> str:
    if video_id == "custom" or audio_id == "custom":
        label = "Пользовательский"
    else:
        v_name = video.name if video else (video_id or "video")
        a_name = audio.name if audio else (audio_id or "audio")
        label = f"{v_name} / {a_name}"
    if normalize_audio_downmix(audio_downmix) == "stereo":
        label += " · stereo downmix"
    return label


class PresetManager:
    """Менеджер слоёв video / audio / subtitles / data."""

    LAYER_DIRS = {
        "video": "video",
        "audio": "audio",
        "subtitles": "subtitles",
        "data": "data",
    }

    def __init__(self, presets_dir: str | None = None):
        self._development_generator = presets_dir is not None
        if presets_dir is None:
            self.presets_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'builtin_presets')
        else:
            self.presets_dir = presets_dir

        self.videos: dict[str, VideoPreset] = {}
        self.audios: dict[str, AudioPreset] = {}
        self.subtitles: dict[str, SubtitlesPreset] = {}
        self.data: dict[str, DataPreset] = {}
        self.default_video_id: str = ""
        self.default_audio_id: str = ""
        self.default_subtitles_id: str = ""
        self.default_data_id: str = ""
        self.load_presets()

    def layer_dir(self, layer: str) -> str:
        return os.path.join(self.presets_dir, self.LAYER_DIRS[layer])

    def load_presets(self) -> None:
        if not os.path.exists(self.presets_dir):
            if not self._development_generator:
                raise ValueError("Встроенные пресеты отсутствуют")
            self.create_default_presets()
            return

        self.videos = self._load_layer(self.layer_dir("video"), VideoPreset)
        self.audios = self._load_layer(self.layer_dir("audio"), AudioPreset)
        self.subtitles = self._load_layer(
            self.layer_dir("subtitles"), SubtitlesPreset
        )
        self.data = self._load_layer(self.layer_dir("data"), DataPreset)

        missing = {
            layer
            for layer, items in (
                ("video", self.videos),
                ("audio", self.audios),
                ("subtitles", self.subtitles),
                ("data", self.data),
            )
            if not items
        }
        if missing and not self._development_generator:
            raise ValueError("Встроенные слои отсутствуют: " + ", ".join(sorted(missing)))
        if missing:
            # Только пустые слои — не перезаписываем YAML остальных
            self.create_default_presets(layers=missing)

        if not self.videos or not self.audios or not self.subtitles or not self.data:
            raise ValueError(
                "Не удалось загрузить слои пресетов "
                f"(video={len(self.videos)}, audio={len(self.audios)}, "
                f"subtitles={len(self.subtitles)}, data={len(self.data)})"
            )

        self.default_video_id = self._require_default("video", self.videos)
        self.default_audio_id = self._require_default("audio", self.audios)
        self.default_subtitles_id = self._require_default(
            "subtitles", self.subtitles
        )
        self.default_data_id = self._require_default("data", self.data)

    @staticmethod
    def _load_layer(directory: str, cls) -> dict:
        items = {}
        if not os.path.isdir(directory):
            return items
        try:
            filenames = sorted(os.listdir(directory))
        except Exception as exc:
            print(f"Ошибка чтения каталога {directory}: {exc}")
            raise
        for filename in filenames:
            if not (filename.endswith(".yaml") or filename.endswith(".yml")):
                continue
            path = os.path.join(directory, filename)
            with open(path, "r", encoding="utf-8") as f:
                payload = yaml.load(f, Loader=_SAFE_YAML_LOADER)
            if not payload:
                continue
            if "id" not in payload or "name" not in payload:
                raise ValueError(
                    f"Пресет {filename}: нужны поля id и name"
                )
            item = cls.from_dict(payload)
            items[item.id] = item
        return items

    @staticmethod
    def _require_default(layer: str, items: dict) -> str:
        defaults = [item.id for item in items.values() if item.is_default]
        if len(defaults) == 1:
            return defaults[0]
        if len(defaults) == 0:
            msg = f"Слой {layer}: нет пресета с default: true"
        else:
            msg = (
                f"Слой {layer}: ожидался ровно один default: true, "
                f"найдено {len(defaults)}: {', '.join(defaults)}"
            )
        print(f"ОШИБКА пресетов: {msg}")
        raise ValueError(msg)

    def _save_yaml(self, directory: str, preset_id: str, data: dict) -> None:
        try:
            os.makedirs(directory, exist_ok=True)
            path = os.path.join(directory, f"{preset_id}.yaml")
            with open(path, "w", encoding="utf-8") as f:
                yaml.dump(
                    data,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                    indent=2,
                )
        except Exception as exc:
            print(f"Ошибка сохранения пресета {preset_id}: {exc}")

    def create_default_presets(self, layers: set[str] | None = None) -> None:
        """Пишет дефолтные YAML только для указанных слоёв (по умолчанию — все)."""
        if not self._development_generator:
            raise TypeError('Генерация YAML доступна только для явно выбранного dev каталога')
        targets = set(layers) if layers is not None else set(self.LAYER_DIRS)
        unknown = targets - set(self.LAYER_DIRS)
        if unknown:
            raise ValueError(f"Неизвестные слои пресетов: {', '.join(sorted(unknown))}")

        if "video" in targets:
            videos = [
                VideoPreset(
                    id="hevc_rip",
                    name="HEVC rip",
                    description="WEBRip / HDTV / BDRip — libx265 CRF 23 slow",
                    is_default=False,
                    video_codec="libx265",
                    preset="slow",
                    crf="23",
                    pixel_format="yuv420p10le",
                    x265_params="level=5.1:ref=6",
                    tune="animation",
                    output_format="matroska",
                    video_map="0:v:0",
                ),
                VideoPreset(
                    id="hevc_auto_level_rip",
                    name="HEVC rip auto level",
                    description="WEBRip / HDTV / BDRip — libx265 auto level CRF 23 slow",
                    is_default=True,
                    video_codec="libx265",
                    preset="slow",
                    crf="23",
                    pixel_format="yuv420p10le",
                    tune="animation",
                    output_format="matroska",
                    video_map="0:v:0",
                ),
                VideoPreset(
                    id="hevc_remux",
                    name="HEVC remux",
                    description="Blu-ray remux quality — libx265 CRF 21 slower",
                    is_default=False,
                    video_codec="libx265",
                    preset="slower",
                    crf="21",
                    pixel_format="yuv420p10le",
                    x265_params="level=5.1:ref=6",
                    tune="animation",
                    output_format="matroska",
                    video_map="0:v:0",
                ),
            ]
            self.videos = {v.id: v for v in videos}
            self.default_video_id = "hevc_auto_level_rip"
            for v in videos:
                self._save_yaml(self.layer_dir("video"), v.id, v.to_dict())

        if "audio" in targets:
            audios = [
                AudioPreset(
                    id="copy", name="Copy",
                    description="Копировать аудио без перекодирования",
                    is_default=True, codec="copy",
                ),
                AudioPreset(
                    id="opus", name="Opus", description="Opus VBR 160k",
                    is_default=False, codec="libopus", bitrate="160k",
                    sample_rate="48000", vbr=True,
                ),
                AudioPreset(
                    id="aac", name="AAC", description="AAC 192k",
                    is_default=False, codec="aac", bitrate="192k",
                    sample_rate="48000",
                ),
                AudioPreset(
                    id="aac_fdk", name="AAC FDK",
                    description="AAC через libfdk_aac 192k (нужен nonfree FFmpeg)",
                    is_default=False, codec="libfdk_aac", bitrate="192k",
                    sample_rate="48000",
                ),
                AudioPreset(
                    id="none", name="None", description="Без аудио",
                    is_default=False, codec=None, audio_map=None,
                ),
            ]
            self.audios = {a.id: a for a in audios}
            self.default_audio_id = "copy"
            for a in audios:
                self._save_yaml(self.layer_dir("audio"), a.id, a.to_dict())

        if "subtitles" in targets:
            subtitles = [
                SubtitlesPreset(
                    id="copy", name="Copy", description="Копировать субтитры",
                    is_default=True,
                ),
                SubtitlesPreset(
                    id="none", name="None", description="Без субтитров",
                    is_default=False, subtitle_map=None, subtitle_codec=None,
                ),
            ]
            self.subtitles = {s.id: s for s in subtitles}
            self.default_subtitles_id = "copy"
            for s in subtitles:
                self._save_yaml(self.layer_dir("subtitles"), s.id, s.to_dict())

        if "data" in targets:
            data = [
                DataPreset(
                    id="copy", name="Copy", description="Копировать data-потоки",
                    is_default=True,
                ),
                DataPreset(
                    id="none", name="None", description="Без data-потоков",
                    is_default=False, data_map=None, data_codec=None,
                ),
            ]
            self.data = {d.id: d for d in data}
            self.default_data_id = "copy"
            for d in data:
                self._save_yaml(self.layer_dir("data"), d.id, d.to_dict())
    def get_video(self, video_id: str) -> VideoPreset | None:
        return self.videos.get((video_id or "").strip()) if video_id else None

    def get_audio(self, audio_id: str) -> AudioPreset | None:
        return self.audios.get((audio_id or "").strip()) if audio_id else None

    def get_subtitles(self, subtitles_id: str) -> SubtitlesPreset | None:
        return (
            self.subtitles.get((subtitles_id or "").strip())
            if subtitles_id
            else None
        )

    def get_data(self, data_id: str) -> DataPreset | None:
        return self.data.get((data_id or "").strip()) if data_id else None

    def _effective_mux_ids(
        self,
        audio_id: str,
        subtitles_id: str | None,
        data_id: str | None,
    ) -> tuple[str, str]:
        """audio=none → subtitles/data none; иначе default из YAML (или явный id)."""
        audio = self.audios.get(audio_id)
        if audio is not None and (audio.codec is None or audio.audio_map is None):
            if "none" not in self.subtitles:
                raise ValueError(
                    "audio_id=none требует пресет subtitles_id=none, "
                    "но он отсутствует"
                )
            if "none" not in self.data:
                raise ValueError(
                    "audio_id=none требует пресет data_id=none, "
                    "но он отсутствует"
                )
            return "none", "none"
        sid = (subtitles_id or self.default_subtitles_id).strip()
        did = (data_id or self.default_data_id).strip()
        return sid or self.default_subtitles_id, did or self.default_data_id

    def resolve(
        self,
        video_id: str | None = None,
        audio_id: str | None = None,
        audio_downmix: str = "none",
        subtitles_id: str | None = None,
        data_id: str | None = None,
    ) -> dict:
        vid = (video_id or self.default_video_id).strip() or self.default_video_id
        aid = (audio_id or self.default_audio_id).strip() or self.default_audio_id
        sid, did = self._effective_mux_ids(aid, subtitles_id, data_id)

        video = self.videos.get(vid)
        if not video:
            raise ValueError(f"Неизвестный video_id: {video_id}")
        audio = self.audios.get(aid)
        if not audio:
            raise ValueError(f"Неизвестный audio_id: {audio_id}")
        subs = self.subtitles.get(sid)
        if not subs:
            raise ValueError(f"Неизвестный subtitles_id: {sid}")
        data = self.data.get(did)
        if not data:
            raise ValueError(f"Неизвестный data_id: {did}")

        return merge_ffmpeg_params(video, audio, subs, data, audio_downmix)

    def resolve_request(
        self,
        video_id: str | None = None,
        audio_id: str | None = None,
        audio_downmix: str = "none",
        subtitles_id: str | None = None,
        data_id: str | None = None,
    ) -> tuple[dict, str, str, str, VideoPreset, AudioPreset]:
        vid = (video_id or self.default_video_id).strip() or self.default_video_id
        aid = (audio_id or self.default_audio_id).strip() or self.default_audio_id
        downmix = normalize_audio_downmix(audio_downmix)
        params = self.resolve(vid, aid, downmix, subtitles_id, data_id)
        return params, vid, aid, downmix, self.videos[vid], self.audios[aid]

    def get_all_videos(self) -> list[VideoPreset]:
        items = list(self.videos.values())
        items.sort(
            key=lambda v: (0 if v.is_default else 1, v.name, v.id)
        )
        return items

    def get_all_audios(self) -> list[AudioPreset]:
        order = {"copy": 0, "opus": 1, "aac": 2, "aac_fdk": 3, "none": 4}
        items = list(self.audios.values())
        items.sort(key=lambda a: (order.get(a.id, 99), a.name, a.id))
        return items

    def get_all_subtitles(self) -> list[SubtitlesPreset]:
        items = list(self.subtitles.values())
        items.sort(key=lambda s: (0 if s.is_default else 1, s.name, s.id))
        return items

    def get_all_data(self) -> list[DataPreset]:
        items = list(self.data.values())
        items.sort(key=lambda d: (0 if d.is_default else 1, d.name, d.id))
        return items

    def reload_presets(self) -> None:
        self.videos.clear()
        self.audios.clear()
        self.subtitles.clear()
        self.data.clear()
        self.load_presets()



class CatalogSnapshot(PresetManager):
    """Один неизменяемый набор моделей и происхождения на всю операцию."""
    def __init__(self, definitions, defaults, details, revision, selections):
        from types import MappingProxyType
        self.catalog_revision = revision
        self.selections = MappingProxyType(dict(selections))
        self._details = details
        classes = {'video': VideoPreset, 'audio': AudioPreset,
                   'subtitles': SubtitlesPreset, 'data': DataPreset}
        for layer, attribute in (('video', 'videos'), ('audio', 'audios'),
                                 ('subtitles', 'subtitles'), ('data', 'data')):
            items = {preset_id: classes[layer].from_dict(dict(definition, id=preset_id,
                      default=preset_id == defaults[layer]))
                     for preset_id, definition in definitions[layer].items()}
            object.__setattr__(self, attribute, MappingProxyType(items))
            object.__setattr__(self, f'default_{layer}_id', defaults[layer])
        self._frozen = True

    def __setattr__(self, name, value):
        if getattr(self, '_frozen', False):
            raise AttributeError('CatalogSnapshot неизменяем')
        object.__setattr__(self, name, value)

    def provenance(self, layer, preset_id):
        import copy
        return copy.deepcopy(self._details[(layer, preset_id)])

    def selection_snapshot(self, video_id=None, audio_id=None, audio_downmix='none'):
        vid = video_id or self.default_video_id
        aid = audio_id or self.default_audio_id
        sid, did = self._effective_mux_ids(aid, None, None)
        layers = {}
        for layer, preset_id in (('video', vid), ('audio', aid), ('subtitles', sid), ('data', did)):
            detail = self.provenance(layer, preset_id)
            layers[layer] = {key: detail[key] for key in ('id', 'name', 'source', 'fingerprint')}
        return {'catalog_revision': self.catalog_revision, 'layers': layers,
                'label': display_label(self.get_video(vid), self.get_audio(aid), audio_downmix)}

    def reload_presets(self):
        raise TypeError('Снимок нельзя перезагружать')

    def load_presets(self):
        raise TypeError('Снимок нельзя перезагружать')

    def create_default_presets(self, *args, **kwargs):
        raise TypeError('Встроенные пресеты доступны только для чтения')

    def _save_yaml(self, *args, **kwargs):
        raise TypeError('Встроенные пресеты доступны только для чтения')


class CatalogPresetManager:
    """Стабильный фасад: ссылки импортирующих модулей переживают все мутации."""
    def __init__(self, *, builtin_dir=None, user_path=None):
        import threading
        self._builtin_dir = builtin_dir
        self._user_path = user_path
        self._services = {}
        self._services_lock = threading.RLock()

    @property
    def service(self):
        from app.preset_catalog import PresetCatalog
        from app.preset_store import configured_path
        path = self._user_path if self._user_path is not None else configured_path()
        key = os.path.abspath(path)
        with self._services_lock:
            if key not in self._services:
                self._services[key] = PresetCatalog(builtin_dir=self._builtin_dir, user_path=key)
            return self._services[key]

    def snapshot(self):
        return self.service.snapshot()

    @property
    def ready(self):
        from app.preset_schema import CatalogError
        try:
            return self.service.ready
        except CatalogError:
            return False

    @property
    def error(self):
        from app.preset_schema import CatalogError
        try:
            return self.service.error.as_dict() if self.service.error else None
        except CatalogError as exc:
            return exc.as_dict()

    @property
    def catalog_revision(self):
        return self.snapshot().catalog_revision

    def reload_presets(self):
        return self.service.reload()

    def __getattr__(self, name):
        return getattr(self.snapshot(), name)


preset_manager = CatalogPresetManager()

# Совместимость импортов: runtime-код должен брать defaults из snapshot().
DEFAULT_VIDEO_ID = 'hevc_auto_level_rip'
DEFAULT_AUDIO_ID = 'copy'
DEFAULT_SUBTITLES_ID = 'copy'
DEFAULT_DATA_ID = 'copy'
