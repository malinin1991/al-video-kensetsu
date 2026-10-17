"""Каталог full overrides и атомарные операции под одним RLock."""
from __future__ import annotations

import copy
from pathlib import Path
import threading
import uuid

from app.preset_schema import (CatalogError, FIELDS, LAYERS, canonical, empty_state,
    fingerprint, normalize_definition, normalize_state, parse_json, parse_yaml, validate_id)
from app.preset_store import PresetStore

BUILTIN_DIR = Path(__file__).parent / 'builtin_presets'


def _load_yaml_definition(path, layer):
    payload = parse_yaml(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise CatalogError('Ожидается объект')
    unknown = set(payload) - set(FIELDS[layer]) - {'name', 'description', 'id', 'default'}
    if unknown:
        raise CatalogError(f'Неизвестные поля: {", ".join(sorted(unknown))}',
                           fields={key: 'Неизвестное поле' for key in sorted(unknown)})
    preset_id = validate_id(payload.get('id'))
    default = payload.get('default', False)
    if not isinstance(default, bool):
        raise CatalogError('default должен быть boolean', fields={'default': 'Ожидается boolean'})
    values = {k: v for k, v in payload.items() if k not in ('id', 'default')}
    # Материализуем именно семантику прежнего loader, без новой базы.
    old_defaults = {'audio': {'codec': 'copy', 'audio_map': '0:a?'},
                    'subtitles': {'subtitle_codec': 'copy', 'subtitle_map': '0:s?'},
                    'data': {'data_codec': 'copy', 'data_map': '0:t?'}}
    for key, value in old_defaults.get(layer, {}).items():
        values.setdefault(key, value)
    return preset_id, default, normalize_definition(layer, values, legacy=True)


def load_yaml_catalog(directory, *, collect_errors=False):
    definitions, defaults, errors = {}, {}, []
    for layer in LAYERS:
        definitions[layer], selected = {}, []
        for path in sorted((Path(directory) / layer).glob('*')):
            if path.suffix.lower() not in ('.yaml', '.yml'):
                continue
            try:
                preset_id, default, definition = _load_yaml_definition(path, layer)
                if preset_id in definitions[layer]:
                    raise CatalogError(f'Повторяющийся ID {preset_id}', fields={'id': 'ID должен быть уникален внутри слоя'})
            except (CatalogError, OSError, UnicodeError) as exc:
                relative = f'{layer}/{path.name}'
                if not collect_errors:
                    raise CatalogError(f'{relative}: {exc}') from exc
                errors.append({'path': relative, 'error': str(exc),
                               'fields': exc.fields if isinstance(exc, CatalogError) else {}})
                continue
            definitions[layer][preset_id] = definition
            if default:
                selected.append(preset_id)
        if len(selected) != 1:
            message = f'Слой {layer}: требуется ровно один default: true'
            if not collect_errors:
                raise CatalogError(message)
            errors.append({'path': layer, 'error': message, 'fields': {'default': selected}})
        else:
            defaults[layer] = selected[0]
    if errors:
        raise CatalogError('Миграция отменена: исправьте ошибки исходных YAML и повторите preview',
                           'legacy_validation_failed', 400, {'sources': errors})
    return definitions, defaults


def available(layer, definition):
    if layer != 'audio' or definition.get('codec') != 'libfdk_aac':
        return True
    from app.ffmpeg_bootstrap import has_libfdk_aac
    return has_libfdk_aac()


class PresetCatalog:
    def __init__(self, *, builtin_dir=None, user_path=None):
        self.lock = threading.RLock()
        self.builtin_dir = Path(builtin_dir) if builtin_dir else BUILTIN_DIR
        self.store = PresetStore(user_path)
        self._snapshot = None
        self.error = None
        self._state = empty_state()
        self.reload()

    @property
    def ready(self):
        return self._snapshot is not None

    def reload(self):
        with self.lock:
            try:
                builtin, defaults = load_yaml_catalog(self.builtin_dir)
                state = self.store.load()
                snapshot = self._build(state, builtin, defaults)
                self._builtin, self._builtin_defaults = builtin, defaults
                self._state, self._snapshot, self.error = state, snapshot, None
            except (CatalogError, OSError) as exc:
                self.error = exc if isinstance(exc, CatalogError) and exc.status == 503 else CatalogError(str(exc), 'catalog_unavailable', 503)
            return self.ready

    def snapshot(self):
        with self.lock:
            if self._snapshot is None:
                raise self.error or CatalogError('Каталог недоступен', 'storage_unavailable', 503)
            return self._snapshot

    def _build(self, state, builtin=None, builtin_defaults=None):
        from app.models.presets import CatalogSnapshot
        builtin = self._builtin if builtin is None else builtin
        builtin_defaults = self._builtin_defaults if builtin_defaults is None else builtin_defaults
        definitions = copy.deepcopy(builtin)
        indexed = {(e['layer'], e['id']): e for e in state['entries']}
        for entry in state['entries']:
            definitions[entry['layer']][entry['id']] = copy.deepcopy(entry['definition'])
        defaults = {layer: state['defaults'][layer] or builtin_defaults[layer] for layer in LAYERS}
        for layer in LAYERS:
            if defaults[layer] not in definitions[layer]:
                raise CatalogError(f'{layer}: default {defaults[layer]} отсутствует')
        for layer, codec_field, map_field in (('audio', 'codec', 'audio_map'), ('subtitles', 'subtitle_codec', 'subtitle_map'), ('data', 'data_codec', 'data_map')):
            none = definitions[layer].get('none')
            if not none or none[codec_field] is not None or none[map_field] is not None:
                raise CatalogError(f'{layer}/none должен существовать и оставаться пустым')
        details = {}
        revision = fingerprint({'schema_version': 1, 'builtin': builtin, 'builtin_defaults': builtin_defaults, 'user': state})
        for layer in LAYERS:
            for preset_id, definition in definitions[layer].items():
                entry, base = indexed.get((layer, preset_id)), builtin[layer].get(preset_id)
                saved_base = entry.get('base_snapshot') if entry else None
                source = entry['kind'] if entry else 'builtin'
                changed = bool(entry and base is not None and entry.get('base_fingerprint') != fingerprint(base))
                differences = {}
                for field in definition:
                    values = {'base': saved_base.get(field) if saved_base else None,
                              'builtin': base.get(field) if base else None, 'user': definition[field]}
                    if len({fingerprint(v) for v in values.values()}) > 1:
                        differences[field] = values
                details[(layer, preset_id)] = {
                    'id': preset_id, 'name': definition['name'], 'definition': copy.deepcopy(definition),
                    'source': source, 'default': defaults[layer] == preset_id,
                    'fingerprint': fingerprint(definition), 'orphaned': bool(entry and source == 'override' and base is None),
                    'builtin_changed': changed, 'base_unknown': bool(entry and source == 'override' and saved_base is None),
                    'source_collision': bool(entry and source == 'custom' and base is not None),
                    'current_builtin': copy.deepcopy(base), 'base_snapshot': copy.deepcopy(saved_base),
                    'differences': differences,
                    'actions': {'reset': source == 'override' and base is not None,
                                'delete': bool(entry and (source == 'custom' or base is None)), 'edit': True},
                }
        return CatalogSnapshot(definitions, defaults, details, revision, state['defaults'])

    def describe(self, snapshot=None):
        snapshot = snapshot or self.snapshot()
        layers, warnings = {}, []
        for layer, attribute in (('video', 'videos'), ('audio', 'audios'), ('subtitles', 'subtitles'), ('data', 'data')):
            layers[layer] = []
            for preset_id in getattr(snapshot, attribute):
                detail = snapshot.provenance(layer, preset_id)
                detail['available'] = available(layer, detail['definition'])
                if not detail['available']:
                    detail['unavailable_reason'] = 'Кодек libfdk_aac недоступен в текущей сборке FFmpeg'
                if detail['orphaned'] or detail['base_unknown'] or detail['source_collision'] or detail['builtin_changed']:
                    warnings.append({'layer': layer, 'id': preset_id, 'orphaned': detail['orphaned'], 'base_unknown': detail['base_unknown'], 'source_collision': detail['source_collision'], 'builtin_changed': detail['builtin_changed']})
                layers[layer].append(detail)
        if Path('/app/presets').is_dir():
            warnings.append({'code': 'legacy_presets_present', 'message': 'Найден /app/presets: выполните явный импорт старых YAML.'})
        return {'layers': layers, 'defaults': {l: getattr(snapshot, f'default_{l}_id') for l in LAYERS},
                'selections': dict(snapshot.selections), 'catalog_revision': snapshot.catalog_revision,
                'warnings': warnings, 'storage': {'ready': self.ready, 'error': self.error.as_dict() if self.error else None}}

    def detail(self, layer, preset_id, snapshot=None):
        if layer not in LAYERS:
            raise CatalogError(f'Неизвестный слой: {layer}', 'not_found', 404)
        snapshot = snapshot or self.snapshot()
        try:
            detail = snapshot.provenance(layer, preset_id)
        except KeyError as exc:
            raise CatalogError('Пресет не найден', 'not_found', 404) from exc
        detail['available'] = available(layer, detail['definition'])
        detail['catalog_revision'] = snapshot.catalog_revision
        return detail

    def _check_match(self, if_match):
        revision = self.snapshot().catalog_revision
        if if_match is None:
            raise CatalogError('Требуется If-Match', 'if_match_required', 428)
        if if_match != revision and if_match != f'"{revision}"':
            raise CatalogError('Пресеты изменены в другой вкладке. Обновите данные и сравните правки.', 'catalog_revision_conflict', 412)

    def _mutate(self, if_match, operation):
        with self.lock:
            self._check_match(if_match)
            if self.error:
                raise self.error
            candidate = copy.deepcopy(self._state)
            operation(candidate)
            candidate = normalize_state(candidate)
            self._build(candidate)
            if candidate == self._state:
                return self._snapshot
            candidate['revision'] = str(uuid.uuid4())
            snapshot = self._build(candidate)
            try:
                self.store.save(self._state, candidate)
            except CatalogError as exc:
                if exc.code == 'save_result_unknown':
                    try:
                        disk_state = self.store.load()
                        self._snapshot = self._build(disk_state)
                        self._state = disk_state
                    except CatalogError as read_exc:
                        self.error = read_exc
                raise
            self._state, self._snapshot, self.error = candidate, snapshot, None
            return snapshot

    def _set_default(self, state, layer, preset_id, *, check_available=True):
        if layer not in LAYERS:
            raise CatalogError('Неизвестный слой', 'not_found', 404)
        if preset_id is not None:
            validate_id(preset_id)
        target_id = preset_id or self._builtin_defaults[layer]
        candidate = self._build(state)
        try:
            definition = candidate.provenance(layer, target_id)['definition']
        except KeyError as exc:
            raise CatalogError('Default ссылается на отсутствующий пресет', 'default_conflict', 409) from exc
        if check_available and not available(layer, definition):
            raise CatalogError('Недоступный кодек нельзя назначить default', 'codec_unavailable', 409)
        state['defaults'][layer] = preset_id

    def create(self, layer, definition, if_match, set_default=False):
        definition = normalize_definition(layer, definition)
        preset_id = 'user_' + str(uuid.uuid4())
        def operation(state):
            state['entries'].append({'layer': layer, 'id': preset_id, 'kind': 'custom', 'definition': definition, 'base_snapshot': None, 'base_fingerprint': None})
            if set_default:
                self._set_default(state, layer, preset_id)
        snapshot = self._mutate(if_match, operation)
        return self.detail(layer, preset_id, snapshot)

    def update(self, layer, preset_id, definition, if_match, set_default=False):
        definition = normalize_definition(layer, definition)
        def operation(state):
            self.detail(layer, preset_id)
            entry = next((e for e in state['entries'] if (e['layer'], e['id']) == (layer, preset_id)), None)
            if entry is None:
                base = self._builtin[layer][preset_id]
                entry = {'layer': layer, 'id': preset_id, 'kind': 'override', 'base_snapshot': copy.deepcopy(base), 'base_fingerprint': fingerprint(base)}
                state['entries'].append(entry)
            entry['definition'] = definition
            if set_default:
                self._set_default(state, layer, preset_id)
        snapshot = self._mutate(if_match, operation)
        return self.detail(layer, preset_id, snapshot)

    def reset(self, layer, preset_id, if_match):
        def operation(state):
            detail = self.detail(layer, preset_id)
            if detail['source'] == 'builtin':
                return
            if not detail['actions']['reset']:
                raise CatalogError('Для этого пресета сброс недоступен', 'reset_conflict', 409)
            state['entries'] = [e for e in state['entries'] if (e['layer'], e['id']) != (layer, preset_id)]
        snapshot = self._mutate(if_match, operation)
        return self.detail(layer, preset_id, snapshot)

    def delete(self, layer, preset_id, if_match, replacement_default_id=None):
        def operation(state):
            detail = self.detail(layer, preset_id)
            if not detail['actions']['delete']:
                raise CatalogError('Встроенный пресет нельзя удалить; используйте сброс', 'delete_conflict', 409)
            if detail['default']:
                if not replacement_default_id or replacement_default_id == preset_id:
                    raise CatalogError('Требуется replacement_default_id', 'default_conflict', 409)
                self._set_default(state, layer, replacement_default_id)
            state['entries'] = [e for e in state['entries'] if (e['layer'], e['id']) != (layer, preset_id)]
        snapshot = self._mutate(if_match, operation)
        return self.describe(snapshot)

    def set_defaults(self, defaults, if_match):
        if not isinstance(defaults, dict) or not defaults:
            raise CatalogError('defaults: требуется непустой объект')
        def operation(state):
            for layer, value in defaults.items():
                self._set_default(state, layer, value)
        snapshot = self._mutate(if_match, operation)
        return self.describe(snapshot)

    def export(self):
        return self.export_with_revision()[0]

    def export_with_revision(self):
        with self.lock:
            snapshot = self.snapshot()
            return copy.deepcopy(self._state), snapshot.catalog_revision

    def _import_candidate(self, catalog, mode, import_defaults=False):
        if isinstance(catalog, str):
            catalog = parse_json(catalog)
        imported = normalize_state(catalog)
        if mode not in ('merge', 'replace_user_catalog'):
            raise CatalogError('mode: ожидается merge/replace_user_catalog')
        candidate = copy.deepcopy(self._state)
        additions, changes, conflicts = [], [], []
        current = {(e['layer'], e['id']): e for e in candidate['entries']}
        incoming = {(e['layer'], e['id']): e for e in imported['entries']}
        for key, entry in incoming.items():
            if key not in current:
                additions.append('/'.join(key))
            elif current[key] != entry:
                changes.append('/'.join(key))
                if mode == 'merge':
                    conflicts.append('/'.join(key))
        default_changes = {}
        if mode == 'replace_user_catalog':
            candidate.update(entries=imported['entries'], defaults=imported['defaults'], migrations=imported['migrations'])
        else:
            for key, entry in incoming.items():
                if key not in current:
                    candidate['entries'].append(entry)
            # Migration metadata is inert bookkeeping, never a filesystem instruction.
            existing = {m['digest'] for m in candidate['migrations']}
            candidate['migrations'].extend(m for m in imported['migrations'] if m['digest'] not in existing)
            if import_defaults:
                for layer, value in imported['defaults'].items():
                    if value is not None:
                        old = candidate['defaults'][layer]
                        if old is not None and old != value:
                            conflicts.append(f'defaults/{layer}')
                        else:
                            candidate['defaults'][layer] = value
        for layer in LAYERS:
            if self._state['defaults'][layer] != candidate['defaults'][layer]:
                default_changes[layer] = {'old': self._state['defaults'][layer], 'new': candidate['defaults'][layer]}
        candidate = normalize_state(candidate)
        snapshot = self._build(candidate)
        warnings = self.describe(snapshot)['warnings']
        for layer in LAYERS:
            definition = snapshot.provenance(layer, getattr(snapshot, f'default_{layer}_id'))['definition']
            if not available(layer, definition):
                warnings.append({'code': 'default_codec_unavailable', 'layer': layer})
        digest = fingerprint({'mode': mode, 'import_defaults': import_defaults if mode == 'merge' else False,
                              'target': self.snapshot().catalog_revision, 'candidate': candidate})
        report = {'additions': additions, 'changes': changes, 'conflicts': conflicts,
                  'removals': sorted('/'.join(key) for key in current.keys() - incoming.keys()) if mode == 'replace_user_catalog' else [],
                  'default_changes': default_changes, 'warnings': warnings, 'preview_digest': digest,
                  'catalog_revision': self.snapshot().catalog_revision}
        return candidate, report

    def preview_import(self, catalog, mode, import_defaults=False, if_match=None):
        with self.lock:
            if if_match is not None:
                self._check_match(if_match)
            return self._import_candidate(catalog, mode, import_defaults)[1]

    def import_catalog(self, catalog, mode, preview_digest, if_match, import_defaults=False):
        def operation(state):
            candidate, report = self._import_candidate(catalog, mode, import_defaults)
            if preview_digest != report['preview_digest']:
                raise CatalogError('Предварительный просмотр устарел; повторите проверку', 'preview_conflict', 409)
            if report['conflicts']:
                raise CatalogError('Импорт содержит конфликты ID/default', 'import_conflict', 409, {'conflicts': report['conflicts']})
            state.update(candidate)
        snapshot = self._mutate(if_match, operation)
        return self.describe(snapshot)
