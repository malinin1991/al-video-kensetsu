"""JSON API и страница редактора; CLI без Origin использует те же проверки схемы."""
from __future__ import annotations

import secrets
import ipaddress
import re
from urllib.parse import urlsplit

from flask import Blueprint, current_app, jsonify, render_template, request, session
from werkzeug.exceptions import RequestEntityTooLarge

from app.models.presets import preset_manager
from app.preset_schema import CatalogError, MAX_BYTES, canonical, object_fields, parse_json

preset_editor_bp = Blueprint('preset_editor', __name__)


def csrf_token():
    token = session.get('preset_editor_csrf')
    if not token:
        token = secrets.token_urlsafe(32)
        session['preset_editor_csrf'] = token
    return token


@preset_editor_bp.get('/presets')
def editor_page():
    return render_template('presets.html', csrf_token=csrf_token())


@preset_editor_bp.errorhandler(CatalogError)
def catalog_error(exc):
    payload = exc.to_dict()
    if preset_manager.ready:
        payload['catalog_revision'] = preset_manager.catalog_revision
    return jsonify(payload), exc.status


@preset_editor_bp.errorhandler(RequestEntityTooLarge)
def too_large(exc):
    return jsonify(error='Тело запроса превышает 5 MiB', code='body_too_large', fields={}), 413


def origin_identity(value):
    """Сравниваем browser origins без доверия к X-Forwarded-* заголовкам."""
    if (not isinstance(value, str) or not value or any(char.isspace() for char in value)
            or any(char in value for char in ('\\', '?', '#', '%'))):
        raise ValueError('Ожидается один http(s) origin')
    parsed = urlsplit(value)
    if (parsed.scheme not in ('http', 'https') or not parsed.hostname or
            parsed.username is not None or parsed.password is not None or
            parsed.path not in ('', '/') or parsed.query or parsed.fragment or parsed.netloc.endswith(':')):
        raise ValueError('Ожидается http(s) origin без пути, параметров и userinfo')
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError('Недопустимый порт origin')
    hostname = parsed.hostname.encode('idna').decode('ascii').lower()
    if ':' in hostname:
        hostname = ipaddress.IPv6Address(hostname).compressed
    elif len(hostname) > 253 or any(not re.fullmatch(r'[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?', label)
                                 for label in hostname.removesuffix('.').split('.')):
        raise ValueError('Недопустимое имя хоста origin')
    return parsed.scheme, hostname, port or (443 if parsed.scheme == 'https' else 80)


@preset_editor_bp.before_request
def protect_editor_mutations():
    if request.method not in ('POST', 'PUT', 'DELETE'):
        return
    if request.mimetype != 'application/json':
        raise CatalogError('Требуется Content-Type: application/json', 'unsupported_media_type', 415)
    origin = request.headers.get('Origin')
    if origin is not None:
        allowed = set()
        try:
            allowed.add(origin_identity(request.host_url))
        except ValueError:
            # Upstream Host у proxy может быть внутренним именем контейнера,
            # непригодным для публичного origin. Проверяем явную настройку отдельно.
            pass
        public_origin = current_app.config.get('PRESET_EDITOR_PUBLIC_ORIGIN', '')
        if public_origin:
            try:
                allowed.add(origin_identity(public_origin))
            except ValueError as exc:
                raise CatalogError('Некорректный PRESET_EDITOR_PUBLIC_ORIGIN: задайте один http(s) origin без пути',
                                   'invalid_public_origin', 503) from exc
        try:
            incoming = origin_identity(origin)
        except ValueError as exc:
            raise CatalogError('Некорректный Origin', 'csrf_failed', 403) from exc
        if incoming not in allowed:
            raise CatalogError('Запрос с чужого Origin отклонён', 'csrf_failed', 403)
        supplied, expected = request.headers.get('X-CSRF-Token', ''), session.get('preset_editor_csrf', '')
        if not expected or not secrets.compare_digest(supplied, expected):
            raise CatalogError('Недействительный CSRF token. Обновите страницу.', 'csrf_failed', 403)
    elif request.headers.get('Sec-Fetch-Site') == 'cross-site':
        raise CatalogError('Запрос с чужого сайта отклонён', 'csrf_failed', 403)
    if request.content_length is not None and request.content_length > MAX_BYTES:
        raise CatalogError('Тело запроса превышает 5 MiB', 'body_too_large', 413)


def body(allowed):
    # Лимит читается независимо от Flask MAX_CONTENT_LENGTH (он больше для видео).
    raw = request.stream.read(MAX_BYTES + 1)
    payload = parse_json(raw) if raw.strip() else {}
    object_fields(payload, allowed, 'request')
    return payload


def match():
    value = request.headers.get('If-Match')
    if value is None:
        raise CatalogError('Требуется If-Match', 'if_match_required', 428)
    # HTTP strong ETag: один quoted token, ни wildcard, ни weak, ни список.
    if len(value) != 66 or not value.startswith('"') or not value.endswith('"'):
        raise CatalogError('Неверный или устаревший If-Match', 'catalog_revision_conflict', 412)
    return value


def boolean(payload, key, default=False):
    value = payload.get(key, default)
    if not isinstance(value, bool):
        raise CatalogError(f'{key}: ожидается boolean')
    return value


def response(payload, status=200, location=None):
    revision = payload.get('catalog_revision') if isinstance(payload, dict) else None
    revision = revision or preset_manager.catalog_revision
    result = jsonify(payload)
    result.status_code = status
    result.set_etag(revision)
    result.headers['Cache-Control'] = 'no-store'
    if location:
        result.headers['Location'] = location
    return result


def mutation(action):
    before = preset_manager.catalog_revision
    result = action()
    after = preset_manager.catalog_revision
    if before != after:
        socketio = current_app.extensions.get('socketio')
        if socketio:
            try:
                socketio.emit('presets_updated', {'catalog_revision': after})
            except Exception:
                current_app.logger.exception('Каталог сохранён, но уведомление об изменении не доставлено')
    return result


@preset_editor_bp.get('/api/preset-editor')
def catalog_get():
    return response(preset_manager.service.describe())


@preset_editor_bp.get('/api/preset-editor/export')
def catalog_export():
    payload, revision = preset_manager.service.export_with_revision()
    result = current_app.response_class(canonical(payload) + b'\n', mimetype='application/json')
    result.set_etag(revision)
    result.headers['Content-Disposition'] = 'attachment; filename="presets-catalog.json"'
    result.headers['Cache-Control'] = 'no-store'
    return result


@preset_editor_bp.put('/api/preset-editor/defaults')
def defaults_update():
    payload = body(('defaults',))
    return response(mutation(lambda: preset_manager.service.set_defaults(payload.get('defaults'), match())))


@preset_editor_bp.post('/api/preset-editor/import/preview')
def import_preview():
    payload = body(('catalog', 'mode', 'import_defaults'))
    return response(preset_manager.service.preview_import(payload.get('catalog'), payload.get('mode'), boolean(payload, 'import_defaults'), match()))


@preset_editor_bp.post('/api/preset-editor/import')
def catalog_import():
    payload = body(('catalog', 'mode', 'import_defaults', 'preview_digest'))
    return response(mutation(lambda: preset_manager.service.import_catalog(payload.get('catalog'), payload.get('mode'), payload.get('preview_digest'), match(), boolean(payload, 'import_defaults'))))


@preset_editor_bp.get('/api/preset-editor/<layer>/<preset_id>')
def preset_get(layer, preset_id):
    return response(preset_manager.service.detail(layer, preset_id))


@preset_editor_bp.post('/api/preset-editor/<layer>')
def preset_create(layer):
    payload = body(('definition', 'set_default'))
    result = mutation(lambda: preset_manager.service.create(layer, payload.get('definition'), match(), boolean(payload, 'set_default')))
    return response(result, 201, f'/api/preset-editor/{layer}/{result["id"]}')


@preset_editor_bp.put('/api/preset-editor/<layer>/<preset_id>')
def preset_update(layer, preset_id):
    payload = body(('definition', 'set_default'))
    return response(mutation(lambda: preset_manager.service.update(layer, preset_id, payload.get('definition'), match(), boolean(payload, 'set_default'))))


@preset_editor_bp.post('/api/preset-editor/<layer>/<preset_id>/reset')
def preset_reset(layer, preset_id):
    body(())
    return response(mutation(lambda: preset_manager.service.reset(layer, preset_id, match())))


@preset_editor_bp.delete('/api/preset-editor/<layer>/<preset_id>')
def preset_delete(layer, preset_id):
    payload = body(('replacement_default_id',))
    return response(mutation(lambda: preset_manager.service.delete(layer, preset_id, match(), payload.get('replacement_default_id'))))
