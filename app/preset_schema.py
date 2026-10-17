"""Единая строгая схема переносимого пользовательского каталога."""
from __future__ import annotations

import hashlib
import json
import re
import uuid
import unicodedata

import yaml

LAYERS = ('video', 'audio', 'subtitles', 'data')
MAX_BYTES = 5 * 1024 * 1024
FIELDS = {
    'video': ('video_codec', 'output_format', 'preset', 'crf', 'pixel_format', 'x265_params', 'tune', 'video_map'),
    'audio': ('codec', 'bitrate', 'sample_rate', 'vbr', 'audio_map'),
    'subtitles': ('subtitle_map', 'subtitle_codec'),
    'data': ('data_map', 'data_codec'),
}


class CatalogError(ValueError):
    def __init__(self, message, code='invalid_catalog', status=400, fields=None):
        super().__init__(message)
        self.code, self.status, self.fields = code, status, fields or {}

    def to_dict(self):
        return self.as_dict()

    def as_dict(self):
        return {'error': str(self), 'code': self.code, 'fields': self.fields}


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise CatalogError('Значение нельзя представить безопасным JSON') from exc


def fingerprint(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if not isinstance(key, str):
            raise CatalogError('Ключи объектов должны быть строками')
        if key in result:
            raise CatalogError(f'Повторяющийся ключ: {key}')
        result[key] = value
    return result


def parse_json(value):
    try:
        byte_count = len(value.encode('utf-8') if isinstance(value, str) else value)
    except UnicodeError as exc:
        raise CatalogError('Некорректный Unicode в JSON') from exc
    if byte_count > MAX_BYTES:
        raise CatalogError('Каталог превышает 5 MiB', 'body_too_large', 413)
    try:
        return json.loads(value, object_pairs_hook=_unique_pairs, parse_constant=lambda x: (_ for _ in ()).throw(CatalogError(f'Недопустимое число: {x}')))
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, CatalogError):
            raise
        raise CatalogError(f'Некорректный JSON: {exc}') from exc


class UniqueSafeLoader(getattr(yaml, 'CSafeLoader', yaml.SafeLoader)):
    pass


def _yaml_mapping(loader, node, deep=False):
    return _unique_pairs([(loader.construct_object(key, deep=deep), loader.construct_object(value, deep=deep)) for key, value in node.value])


UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _yaml_mapping)


def parse_yaml(value):
    if len(value.encode('utf-8')) > MAX_BYTES:
        raise CatalogError('YAML превышает 5 MiB')
    try:
        return yaml.load(value, Loader=UniqueSafeLoader)
    except (yaml.YAMLError, RecursionError) as exc:
        raise CatalogError(f'Некорректный YAML: {exc}') from exc


def object_fields(value, allowed, context='объект'):
    if not isinstance(value, dict):
        raise CatalogError(f'{context}: ожидается объект')
    if any(not isinstance(key, str) for key in value):
        raise CatalogError(f'{context}: ключи должны быть строками')
    unknown = set(value) - set(allowed)
    if unknown:
        raise CatalogError(f'{context}: неизвестные поля: {", ".join(sorted(unknown))}')


def validate_id(value):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,64}', value) or value == 'custom':
        raise CatalogError('ID: допустимы 1–64 буквы, цифры, _ и -; custom зарезервирован')
    return value


def _text(value, field, limit, optional=False, multiline=False):
    if value is None and optional:
        return None
    if not isinstance(value, str):
        raise CatalogError(f'{field}: ожидается строка', fields={field: 'Ожидается строка'})
    if any(unicodedata.category(c) == 'Cc' and not (multiline and c in '\n\t') for c in value):
        raise CatalogError(f'{field}: управляющие символы запрещены')
    value = value if multiline else value.strip()
    if len(value) > limit:
        raise CatalogError(f'{field}: превышен размер или управляющие символы', fields={field: 'Недопустимая строка'})
    if not value and optional:
        return None
    return value


def _integer(value, field, low, high, suffix=''):
    if value is None or value == '':
        return None
    text = str(value)
    if suffix and text.endswith(suffix):
        text = text[:-len(suffix)]
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]+', text) or not low <= int(text) <= high:
        raise CatalogError(f'{field}: ожидается целое {low}–{high}', fields={field: f'{low}–{high}'})
    return str(int(text)) + suffix


def normalize_definition(layer, definition, *, legacy=False):
    if layer not in LAYERS:
        raise CatalogError(f'Неизвестный слой: {layer}', 'not_found', 404)
    object_fields(definition, ('name', 'description') + FIELDS[layer], layer)
    if len(canonical(definition)) > 64 * 1024:
        raise CatalogError('Определение превышает 64 KiB')
    codec_key, map_key = {'audio': ('codec', 'audio_map'), 'subtitles': ('subtitle_codec', 'subtitle_map'), 'data': ('data_codec', 'data_map')}.get(layer, (None, None))
    if codec_key and definition.get(codec_key) == 'remove':
        definition = dict(definition, **{codec_key: None, map_key: None})
    result = {'name': _text(definition.get('name'), 'name', 120), 'description': _text(definition.get('description', ''), 'description', 4000, multiline=True)}
    if not result['name']:
        raise CatalogError('name: имя не может быть пустым')
    for field in FIELDS[layer]:
        value = definition.get(field)
        if legacy and value in ('null', 'None', 'remove'):
            value = None
        if field == 'vbr':
            if value is not None and not isinstance(value, bool):
                raise CatalogError('vbr: ожидается boolean или null')
        elif field in ('crf', 'sample_rate', 'bitrate'):
            value = _integer(value, field, *{'crf': (0, 51), 'sample_rate': (8000, 192000), 'bitrate': (8, 1536)}[field], suffix='k' if field == 'bitrate' else '')
        else:
            value = _text(value, field, 4096 if field == 'x265_params' else 128, optional=True)
        result[field] = value
    if layer == 'video':
        if result['video_codec'] != 'libx265' or result['output_format'] != 'matroska':
            raise CatalogError('video_codec=libx265 и output_format=matroska обязательны')
        enums = {'preset': ('ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'), 'pixel_format': ('yuv420p', 'yuv420p10le', 'yuv444p10le'), 'tune': ('animation', 'grain', 'psnr', 'ssim', 'fastdecode', 'zerolatency')}
        if result['tune'] == 'none':
            result['tune'] = None
        for field, choices in enums.items():
            if result[field] is not None and result[field] not in choices:
                raise CatalogError(f'{field}: неподдерживаемое значение')
    else:
        codec_field, map_field = {'audio': ('codec', 'audio_map'), 'subtitles': ('subtitle_codec', 'subtitle_map'), 'data': ('data_codec', 'data_map')}[layer]
        if result[codec_field] == 'remove':
            result[codec_field] = result[map_field] = None
        if layer == 'audio' and result[codec_field] == 'opus':
            result[codec_field] = 'libopus'
        choices = {'audio': ('copy', 'aac', 'libfdk_aac', 'libopus'), 'subtitles': ('copy', 'ass', 'srt'), 'data': ('copy',)}[layer]
        if result[codec_field] is not None and result[codec_field] not in choices:
            raise CatalogError(f'{codec_field}: неподдерживаемый кодек')
        if (result[codec_field] is None) != (result[map_field] is None):
            raise CatalogError(f'{codec_field}/{map_field}: заполните оба поля или очистите оба')
        if layer == 'audio' and result[codec_field] in (None, 'copy') and any(result[f] is not None for f in ('bitrate', 'sample_rate', 'vbr')):
            raise CatalogError('copy/без аудио: bitrate, sample_rate и vbr должны быть null')
    map_field = {'video': 'video_map', 'audio': 'audio_map', 'subtitles': 'subtitle_map', 'data': 'data_map'}[layer]
    value = result[map_field]
    stream = {'video': 'v', 'audio': 'a', 'subtitles': 's', 'data': 't'}[layer]
    if value is not None and not re.fullmatch(rf'0:{stream}(?::[0-9]+)?\??', value):
        raise CatalogError(f'{map_field}: допустим только поток 0:{stream}[:N][?]')
    return result


def empty_state():
    return {'schema_version': 1, 'revision': 'empty-v1', 'defaults': dict.fromkeys(LAYERS), 'entries': [], 'migrations': []}


def normalize_state(state):
    object_fields(state, ('schema_version', 'revision', 'defaults', 'entries', 'migrations'), 'catalog')
    if type(state.get('schema_version')) is not int or state['schema_version'] != 1:
        raise CatalogError('Неподдерживаемая версия схемы каталога', 'unsupported_schema', 503)
    revision = state.get('revision')
    if revision != 'empty-v1':
        try:
            uuid.UUID(revision)
        except (ValueError, TypeError, AttributeError) as exc:
            raise CatalogError('revision: ожидается UUID') from exc
    defaults = state.get('defaults')
    object_fields(defaults, LAYERS, 'defaults')
    if set(defaults) != set(LAYERS):
        raise CatalogError('defaults: требуются четыре слоя')
    defaults = {layer: None if defaults[layer] is None else validate_id(defaults[layer]) for layer in LAYERS}
    entries = state.get('entries')
    if not isinstance(entries, list) or len(entries) > 500:
        raise CatalogError('entries: ожидается список до 500 записей')
    normalized, seen = [], set()
    for entry in entries:
        object_fields(entry, ('layer', 'id', 'kind', 'definition', 'base_snapshot', 'base_fingerprint'), 'entry')
        layer, preset_id = entry.get('layer'), validate_id(entry.get('id'))
        if layer not in LAYERS:
            raise CatalogError(f'Неизвестный слой: {layer}')
        key = (layer, preset_id)
        if key in seen:
            raise CatalogError(f'Повторяющийся ID: {layer}/{preset_id}')
        seen.add(key)
        kind = entry.get('kind')
        if kind not in ('custom', 'override'):
            raise CatalogError('kind: ожидается custom/override')
        base = entry.get('base_snapshot')
        if base is not None:
            base = normalize_definition(layer, base)
        base_fp = entry.get('base_fingerprint')
        if base_fp != (fingerprint(base) if base is not None else None):
            raise CatalogError(f'{layer}/{preset_id}: base_fingerprint не совпадает с base_snapshot')
        if kind == 'custom' and base is not None:
            raise CatalogError('custom не может содержать base_snapshot')
        normalized.append({'layer': layer, 'id': preset_id, 'kind': kind, 'definition': normalize_definition(layer, entry.get('definition')), 'base_snapshot': base, 'base_fingerprint': base_fp})
    migrations = state.get('migrations', [])
    if not isinstance(migrations, list) or any(not isinstance(m, dict) or set(m) - {'digest', 'result'} or not re.fullmatch('[0-9a-f]{64}', str(m.get('digest', ''))) or not isinstance(m.get('result'), str) for m in migrations):
        raise CatalogError('migrations: некорректные отметки миграции')
    result = {'schema_version': 1, 'revision': revision, 'defaults': defaults, 'entries': sorted(normalized, key=lambda e: (e['layer'], e['id'])), 'migrations': migrations}
    if len(canonical(result)) > MAX_BYTES:
        raise CatalogError('Каталог превышает 5 MiB', 'body_too_large', 413)
    return result
