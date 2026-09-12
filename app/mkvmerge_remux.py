"""Сборка контейнера после encode: JSON-опции mkvmerge (языки, имена, ник)."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any

from app.file_utils import cleanup_file

logger = logging.getLogger('video_encoder')

ANILIBERTY_AUDIO_TITLE = 'AniLiberty'
SIGN_SUBTITLE_TITLE = 'Надписи'
SUBTITLE_TITLE = 'Субтитры'

_ORIGINAL_PREFIX_RE = re.compile(
    r'^(Original|Оригинал)(?:$|[\s\[\(\-_])',
    re.IGNORECASE,
)
_NICKNAME_CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]')
_NICKNAME_TAG_ESCAPE_RE = re.compile(r'([\\\[\]+])')
_MAX_NICKNAME_LEN = 64
_REGULAR_TRACK_TYPES = frozenset({'video', 'audio', 'subtitles'})

# ISO 639-2 (identify) → ISO 639-1 (как в GUI mkvmerge)
_ISO639_2_TO_1 = {
    'rus': 'ru',
    'jpn': 'ja',
    'eng': 'en',
    'chi': 'zh',
    'zho': 'zh',
    'kor': 'ko',
    'ger': 'de',
    'deu': 'de',
    'fre': 'fr',
    'fra': 'fr',
    'spa': 'es',
    'ita': 'it',
    'por': 'pt',
    'ukr': 'uk',
    'bel': 'be',
    'pol': 'pl',
    'cze': 'cs',
    'ces': 'cs',
}

_UNKNOWN_LANGS = frozenset({'', 'und', 'unk', 'mis', 'zxx'})

def sanitize_nickname(nickname: str) -> str:
    """Нормализует ник: trim, длина, без управляющих символов."""
    cleaned = str(nickname or '').strip()
    if not cleaned:
        return ''
    if len(cleaned) > _MAX_NICKNAME_LEN:
        raise ValueError(f'Ник слишком длинный (макс. {_MAX_NICKNAME_LEN} символов)')
    if _NICKNAME_CONTROL_RE.search(cleaned):
        raise ValueError('Ник не должен содержать управляющие символы (переносы строк и т.п.)')
    return cleaned

def sanitize_video_title(title: str) -> str:
    """Нормализует базовое имя видеодорожки (без суффикса []): trim, длина, без control."""
    cleaned = str(title or '').strip()
    if not cleaned:
        return ''
    if len(cleaned) > _MAX_NICKNAME_LEN:
        raise ValueError(
            f'Название видеодорожки слишком длинное (макс. {_MAX_NICKNAME_LEN} символов)'
        )
    if _NICKNAME_CONTROL_RE.search(cleaned):
        raise ValueError(
            'Название видеодорожки не должно содержать управляющие символы '
            '(переносы строк и т.п.)'
        )
    return cleaned

def escape_nickname_for_tag(nickname: str) -> str:
    """Экранирует \\ [ ] + для безопасной вставки внутрь […]."""
    return _NICKNAME_TAG_ESCAPE_RE.sub(r'\\\1', nickname)

def unescape_nickname_tag(value: str) -> str:
    """Обратная к escape_nickname_for_tag: \\, \\[, \\], \\+ → литералы."""
    out: list[str] = []
    i = 0
    text = value or ''
    while i < len(text):
        if text[i] == '\\' and i + 1 < len(text) and text[i + 1] in '\\[]+':
            out.append(text[i + 1])
            i += 2
            continue
        out.append(text[i])
        i += 1
    return ''.join(out)

def is_original_track_title(title: str) -> bool:
    if not title:
        return False
    return bool(_ORIGINAL_PREFIX_RE.match(title.strip()))

def is_aniliberty_title(title: str) -> bool:
    return (title or '').strip() == ANILIBERTY_AUDIO_TITLE

def is_update_metadata_enabled(params: dict[str, Any] | None) -> bool:
    value = (params or {}).get('update_metadata', True)
    if isinstance(value, bool):
        return value
    if value is None:
        return True
    return str(value).strip().lower() in ('true', '1', 'yes', 'on')

def audio_was_encoded(params: dict[str, Any] | None) -> bool:
    codec = str((params or {}).get('audio_codec') or '').strip().lower()
    return codec not in ('', 'none', 'null', 'copy', 'remove')

MIME_TRUETYPE = 'application/x-truetype-font'
MIME_OPENTYPE = 'application/vnd.ms-opentype'

_FONT_MIME_BY_EXT = {
    '.ttf': MIME_TRUETYPE,
    '.ttc': MIME_TRUETYPE,
    '.otf': MIME_OPENTYPE,
}

def _resolve_tool_bin(env_var: str, tool_name: str) -> str:
    configured = os.environ.get(env_var, '').strip()
    if configured:
        if os.path.isfile(configured):
            return configured
        resolved = shutil.which(configured)
        if resolved:
            return resolved
        raise RuntimeError(
            f'{tool_name} не найден: установите mkvtoolnix или задайте {env_var}'
        )
    found = shutil.which(tool_name)
    if not found:
        raise RuntimeError(
            f'{tool_name} не найден: установите mkvtoolnix или задайте {env_var}'
        )
    return found

def get_mkvmerge_bin() -> str:
    return _resolve_tool_bin('MKVMERGE_BIN', 'mkvmerge')

def get_mkvpropedit_bin() -> str:
    return _resolve_tool_bin('MKVPROPEDIT_BIN', 'mkvpropedit')

def expected_font_mime_type(file_name: str) -> str | None:
    """Ожидаемый MIME шрифта по расширению имени вложения; иначе None."""
    name = str(file_name or '').strip()
    if not name:
        return None
    _, ext = os.path.splitext(name)
    return _FONT_MIME_BY_EXT.get(ext.lower())

def collect_font_mime_fixes(identify: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Вложения-шрифты, у которых content_type не совпадает с ожидаемым MIME.

    Возвращает список dict: id, file_name, current_mime, expected_mime.
    """
    fixes: list[dict[str, Any]] = []
    for item in identify.get('attachments') or []:
        if not isinstance(item, dict):
            continue
        file_name = str(item.get('file_name') or '')
        expected = expected_font_mime_type(file_name)
        if not expected:
            continue
        current = str(item.get('content_type') or '').strip()
        if current.lower() == expected.lower():
            continue
        try:
            attachment_id = int(item.get('id'))
        except (TypeError, ValueError):
            logger.warning(
                'Пропуск вложения без id при проверке MIME: %s',
                file_name or item,
            )
            continue
        fixes.append(
            {
                'id': attachment_id,
                'file_name': file_name,
                'current_mime': current,
                'expected_mime': expected,
            }
        )
    return fixes

def build_mkvpropedit_font_mime_command(
    filepath: str,
    fixes: list[dict[str, Any]],
) -> list[str]:
    """Аргументы mkvpropedit: --attachment-mime-type + --update-attachment по id."""
    if not fixes:
        raise ValueError('Нет правок MIME для mkvpropedit')
    cmd = [get_mkvpropedit_bin(), os.path.abspath(filepath)]
    for fix in fixes:
        cmd.extend(
            [
                '--attachment-mime-type',
                str(fix['expected_mime']),
                '--update-attachment',
                str(int(fix['id'])),
            ]
        )
    return cmd

def fix_font_attachment_mime_types(
    filepath: str,
    *,
    identify: dict[str, Any] | None = None,
    timeout: int = 120,
) -> list[dict[str, Any]]:
    """
    Перед remux: если MIME шрифта не совпадает с правилом по расширению — правит через mkvpropedit.

    .ttf/.ttc → application/x-truetype-font
    .otf → application/vnd.ms-opentype
    """
    if not filepath or not os.path.exists(filepath):
        raise RuntimeError(f'Файл для правки MIME шрифтов не найден: {filepath}')

    data = identify if identify is not None else identify_encoded_file(filepath)
    fixes = collect_font_mime_fixes(data)
    if not fixes:
        return []

    cmd = build_mkvpropedit_font_mime_command(filepath, fixes)
    logger.info(
        'Правка MIME шрифтов (%s): %s',
        len(fixes),
        '; '.join(
            f"{fix['file_name'] or fix['id']}: "
            f"{fix['current_mime'] or '(пусто)'} → {fix['expected_mime']}"
            for fix in fixes
        ),
    )
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        check=False,
    )
    if result.returncode not in (0, 1):
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(
            f'mkvpropedit не смог обновить MIME шрифтов (код {result.returncode}): '
            f'{detail}'
        )
    if result.returncode == 1:
        logger.warning(
            'mkvpropedit завершился с предупреждениями при правке MIME: %s',
            (result.stderr or result.stdout or '').strip(),
        )
    return fixes

def identify_encoded_file(filepath: str, timeout: int = 60) -> dict[str, Any]:
    """Читает дорожки закодированного файла через mkvmerge -J."""
    result = subprocess.run(
        [get_mkvmerge_bin(), '-J', filepath],
        capture_output=True,
        text=True,
        encoding='utf-8',
        errors='replace',
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or '').strip()
        raise RuntimeError(f'mkvmerge -J не смог прочитать файл: {detail}')

    try:
        data = json.loads(result.stdout or '{}')
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'mkvmerge -J вернул невалидный JSON: {exc}') from exc

    if not isinstance(data, dict):
        raise RuntimeError('mkvmerge -J вернул неожиданный JSON')

    errors = [str(item) for item in (data.get('errors') or []) if item]
    if errors:
        raise RuntimeError(f'mkvmerge -J: {"; ".join(errors)}')

    container = data.get('container') or {}
    if not container.get('recognized') or not container.get('supported'):
        raise RuntimeError('mkvmerge не распознал закодированный файл')

    return data

def write_option_file(path: str, options: list[str]) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, 'w', encoding='utf-8') as handle:
        json.dump(options, handle, ensure_ascii=False, indent=2)
        handle.write('\n')

def option_file_path(output_path: str, job_id: str = '') -> str:
    """JSON рядом с выходом, либо с id задания в имени."""
    stem, _ = os.path.splitext(output_path)
    if job_id:
        return f'{stem}.{job_id}.mkvmerge.json'
    return f'{stem}.mkvmerge.json'

def remux_temp_path(output_path: str) -> str:
    return f'{output_path}.remux.tmp'

def encode_partial_path(output_path: str) -> str:
    stem, ext = os.path.splitext(output_path)
    return f'{stem}.partial{ext or ".mkv"}'

def job_sidecar_paths(output_path: str, job_id: str = '') -> list[str]:
    """Промежуточные файлы encode/remux: partial, remux.tmp, JSON опций."""
    if not output_path:
        return []
    paths = [
        encode_partial_path(output_path),
        remux_temp_path(output_path),
        option_file_path(output_path, job_id),
    ]
    if job_id:
        without_id = option_file_path(output_path)
        if without_id not in paths:
            paths.append(without_id)
    unique: list[str] = []
    seen: set[str] = set()
    for path in paths:
        if path and path != output_path and path not in seen:
            seen.add(path)
            unique.append(path)
    return unique

def cleanup_job_sidecars(output_path: str, job_id: str = '') -> None:
    """Удаляет sidecar encode/remux, не трогая финальный выход."""
    for path in job_sidecar_paths(output_path, job_id):
        cleanup_file(path)

def build_mkvmerge_options(
    encoded_path: str,
    output_path: str,
    identify: dict[str, Any],
    params: dict[str, Any] | None = None,
) -> list[str]:
    """
    Собирает JSON-массив аргументов mkvmerge по identify закодированного файла.

    Цвет и display dimensions не трогаем — mkvmerge копирует из исходника.
    Языки — ISO 639-1. Default/forced/original ставятся явно по правилам сборки.
    Сегментный Title контейнера всегда очищается (--title "").
    """
    params = params or {}
    nickname = sanitize_nickname(str(params.get('metadata_nickname') or ''))
    video_title = sanitize_video_title(str(params.get('metadata_video_title') or ''))
    audio_encoded = audio_was_encoded(params)

    encoded_abs = os.path.abspath(encoded_path)
    output_abs = os.path.abspath(output_path)

    # Без --ui-language: Debian mkvtoolnix без перевода ru падает с кодом 2.
    # --title "": сегментный Title контейнера (не тег) — всегда очищаем.
    options: list[str] = [
        '--output', output_abs,
        '--title', '',
        '--no-track-tags',
        '--no-global-tags',
        '--enable-legacy-font-mime-types',
    ]

    tracks = _regular_tracks(identify)
    single_video = sum(1 for track in tracks if track.get('type') == 'video') == 1
    subtitle_titles = _resolve_subtitle_titles(tracks)
    for track in tracks:
        options.extend(
            _options_for_track(
                track,
                nickname=nickname,
                audio_encoded=audio_encoded,
                single_video=single_video,
                video_title=video_title,
                subtitle_title=subtitle_titles.get(_track_id(track)),
            )
        )

    options.extend(['(', encoded_abs, ')'])

    if tracks:
        track_order = ','.join(f'0:{_track_id(track)}' for track in tracks)
        options.extend(['--track-order', track_order])

    return options

def _regular_tracks(identify: dict[str, Any]) -> list[dict[str, Any]]:
    tracks = []
    for track in identify.get('tracks') or []:
        if not isinstance(track, dict):
            continue
        if track.get('type') in _REGULAR_TRACK_TYPES:
            tracks.append(track)
    return tracks

def _track_id(track: dict[str, Any]) -> int:
    return int(track.get('id', 0))

def _props(track: dict[str, Any]) -> dict[str, Any]:
    props = track.get('properties')
    return props if isinstance(props, dict) else {}

def _options_for_track(
    track: dict[str, Any],
    *,
    nickname: str,
    audio_encoded: bool,
    single_video: bool,
    video_title: str = '',
    subtitle_title: str | None = None,
) -> list[str]:
    tid = _track_id(track)
    track_type = str(track.get('type') or '')
    props = _props(track)
    title = str(props.get('track_name') or '')
    language = _language_from_props(props)
    if track_type == 'subtitles' and subtitle_title is not None:
        new_title = subtitle_title
        rule_title = subtitle_title
    else:
        if track_type == 'video':
            # Override базового имени — только при единственной video-дорожке
            title = _resolve_video_track_title(
                title,
                video_title if single_video else '',
            )
        elif track_type == 'audio':
            # Пустое имя: default → AniLiberty, иначе → Original
            title = _resolve_audio_track_title(title, track)
        new_title = _apply_nickname(track_type, title, nickname, audio_encoded)
        rule_title = title

    options: list[str] = []

    if track_type == 'subtitles' and _is_text_subtitle(track):
        options.extend(['--sub-charset', f'{tid}:UTF-8'])

    resolved = resolve_language(track_type, rule_title, language)
    if resolved:
        options.extend(['--language', f'{tid}:{resolved}'])

    if new_title:
        options.extend(['--track-name', f'{tid}:{new_title}'])

    options.extend(
        _flag_options_for_track(
            tid,
            track_type,
            rule_title,
            single_video=single_video,
        )
    )

    return options

def _resolve_video_track_title(title: str, override: str = '') -> str:
    """Базовое имя видеодорожки: пустое → Original; override заменяет часть без []."""
    stripped = (title or '').strip()
    if not stripped:
        stripped = 'Original'

    override = (override or '').strip()
    if not override:
        return stripped

    split = _split_trailing_outer_brackets(stripped)
    if split is None:
        return override

    _base, existing = split
    if existing:
        return f'{override} [{existing}]'
    return override

def _resolve_audio_track_title(title: str, track: dict[str, Any]) -> str:
    """Базовое имя аудио: пустое + default → AniLiberty; пустое иначе → Original."""
    stripped = (title or '').strip()
    if stripped:
        return stripped
    if _is_default_track(track):
        return ANILIBERTY_AUDIO_TITLE
    return 'Original'

def _flag_options_for_track(
    tid: int,
    track_type: str,
    title: str,
    *,
    single_video: bool,
) -> list[str]:
    """Явные флаги mkvmerge: исходник может врать, копировать его нельзя."""
    title = (title or '').strip()

    if track_type == 'video':
        flags: list[str] = []
        if single_video:
            flags.extend(_default_forced_flags(tid, enabled=True))
        if is_original_track_title(title):
            flags.extend(['--original-flag', f'{tid}:1'])
        return flags

    if track_type == 'audio' and is_aniliberty_title(title):
        return _default_forced_flags(tid, enabled=True)

    if track_type == 'audio' and is_original_track_title(title):
        return ['--original-flag', f'{tid}:1']

    if track_type == 'subtitles' and title == SIGN_SUBTITLE_TITLE:
        return _default_forced_flags(tid, enabled=True)

    if track_type == 'subtitles' and title == SUBTITLE_TITLE:
        return _default_forced_flags(tid, enabled=False)

    return []

def _resolve_subtitle_titles(tracks: list[dict[str, Any]]) -> dict[int, str]:
    """Имена субтитров по default: «Надписи» / «Субтитры»."""
    subs = [track for track in tracks if track.get('type') == 'subtitles']
    if not subs:
        logger.info(
            'Субтитры в файле отсутствуют: правила именования '
            '«Надписи»/«Субтитры» не применяются'
        )
        return {}

    if len(subs) == 2:
        defaults = [track for track in subs if _is_default_track(track)]
        if len(defaults) == 1:
            default_id = _track_id(defaults[0])
            return {
                _track_id(track): (
                    SIGN_SUBTITLE_TITLE
                    if _track_id(track) == default_id
                    else SUBTITLE_TITLE
                )
                for track in subs
            }
        return {
            _track_id(subs[0]): SIGN_SUBTITLE_TITLE,
            _track_id(subs[1]): SUBTITLE_TITLE,
        }

    return {
        _track_id(track): (
            SIGN_SUBTITLE_TITLE if _is_default_track(track) else SUBTITLE_TITLE
        )
        for track in subs
    }

def _is_default_track(track: dict[str, Any]) -> bool:
    value = _props(track).get('default_track')
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or '').strip().lower() in ('1', 'true', 'yes', 'on')

def _default_forced_flags(tid: int, *, enabled: bool) -> list[str]:
    bit = '1' if enabled else '0'
    return [
        '--default-track-flag', f'{tid}:{bit}',
        '--forced-display-flag', f'{tid}:{bit}',
    ]

def _apply_nickname(
    track_type: str,
    title: str,
    nickname: str,
    audio_encoded: bool,
) -> str:
    if not nickname:
        return title
    if track_type == 'video':
        updated = _merge_video_nickname(title or '', nickname)
        return updated if updated is not None else title
    if track_type == 'audio' and audio_encoded and is_original_track_title(title):
        updated = _append_nickname_suffix(title, nickname)
        return updated if updated is not None else title
    return title

def _is_escaped_at(text: str, index: int) -> bool:
    """True, если символ text[index] экранирован нечётным числом \\ слева."""
    count = 0
    pos = index - 1
    while pos >= 0 and text[pos] == '\\':
        count += 1
        pos -= 1
    return count % 2 == 1

def _split_trailing_outer_brackets(title: str) -> tuple[str, str] | None:
    """Хвостовая внешняя пара [] через подсчёт глубины с конца.

    Экранированные \\[ \\] не влияют на глубину (ник не ломает разбор).
    Содержимое между скобками не интерпретируется и не unescape-ится.
    Если баланс не сходится (лишние ]), берём первую неэкранированную [
    до финальной ] — чтобы абсурдные вложенные теги всё же мержились.
    """
    stripped = (title or '').strip()
    if not stripped.endswith(']'):
        return None
    if _is_escaped_at(stripped, len(stripped) - 1):
        return None

    depth = 0
    open_index: int | None = None
    for index in range(len(stripped) - 1, -1, -1):
        char = stripped[index]
        if char not in '[]' or _is_escaped_at(stripped, index):
            continue
        if char == ']':
            depth += 1
        else:  # '['
            depth -= 1
            if depth == 0:
                open_index = index
                break

    if open_index is None:
        # Несбалансированные лишние ] — внешняя пара от первой [ до EOS ]
        for index, char in enumerate(stripped):
            if char == '[' and not _is_escaped_at(stripped, index):
                open_index = index
                break
        if open_index is None:
            return None

    base = stripped[:open_index].rstrip()
    existing = stripped[open_index + 1 : -1]
    return base, existing

def _nickname_already_present(existing: str, nickname: str, tag_nick: str) -> bool:
    """Идемпотентность: экранированный и «сырой»/разэкранированный ник."""
    if existing == tag_nick or existing.startswith(f'{tag_nick} + '):
        return True
    if existing == nickname:
        return True
    unescaped = unescape_nickname_tag(existing)
    if unescaped == nickname or unescaped.startswith(f'{nickname} + '):
        return True
    return False

def _merge_video_nickname(title: str, nickname: str) -> str | None:
    """Вклеивает ник по хвостовой внешней паре [] (имя дорожки не важно):
    Title → Title [Nick]; Title [Nokt] → Title [Nick + Nokt].
    Пустое имя (после resolve) не ожидается — запасной путь: Original [Nick].
    Содержимое внутри [] сохраняется дословно; в нике \\ [ ] + экранируются.
    """
    tag_nick = escape_nickname_for_tag(nickname)
    stripped = (title or '').strip() or 'Original'

    split = _split_trailing_outer_brackets(stripped)
    if split is None:
        return f'{stripped} [{tag_nick}]'

    base, existing = split
    if not existing:
        return f'{base} [{tag_nick}]' if base else f'Original [{tag_nick}]'

    if _nickname_already_present(existing, nickname, tag_nick):
        return None

    merged_tag = f'{tag_nick} + {existing}'
    if base:
        return f'{base} [{merged_tag}]'
    return f'[{merged_tag}]'

def _append_nickname_suffix(title: str, nickname: str) -> str | None:
    """Простой суффикс для аудио Original (без merge): Title → Title [Nick]."""
    tag_nick = escape_nickname_for_tag(nickname)
    if title.endswith(f' [{tag_nick}]') or title.endswith(f' [{nickname}]'):
        return None
    if not title.strip():
        return f'[{tag_nick}]'
    return f'{title} [{tag_nick}]'

def resolve_language(track_type: str, title: str, current_language: str) -> str | None:
    """ISO 639-1 для --language, или None если флаг не ставить."""
    current = (current_language or '').strip()
    title = (title or '').strip()

    if track_type == 'audio' and is_aniliberty_title(title):
        return 'ru'

    if title in (SIGN_SUBTITLE_TITLE, SUBTITLE_TITLE):
        return 'ru'

    if is_original_track_title(title):
        compressed = compress_language(current)
        if compressed is None:
            return 'ja'
        return compressed

    return compress_language(current)

def compress_language(code: str) -> str | None:
    cleaned = (code or '').strip().lower().replace('_', '-')
    if not cleaned:
        return None
    primary = cleaned.split('-', 1)[0]
    if primary in _UNKNOWN_LANGS:
        return None
    mapped = _ISO639_2_TO_1.get(primary)
    if mapped:
        return mapped
    if primary.isalpha() and len(primary) in (2, 3):
        return primary
    return None

def _language_from_props(props: dict[str, Any]) -> str:
    language = str(props.get('language') or '').strip()
    if language:
        return language
    ietf = str(props.get('language_ietf') or '').strip()
    if ietf:
        return ietf.split('-', 1)[0]
    return ''

def _is_text_subtitle(track: dict[str, Any]) -> bool:
    props = _props(track)
    if props.get('text_subtitles') is True:
        return True
    codec_id = str(props.get('codec_id') or '')
    return codec_id.upper().startswith('S_TEXT/')
