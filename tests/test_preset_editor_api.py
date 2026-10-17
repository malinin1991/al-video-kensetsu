"""HTTP версии, Origin/CSRF, CRUD и переносимый import/export."""
import json
from flask import Flask
import pytest

from app.models.presets import preset_manager
from app.preset_routes import preset_editor_bp


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr('app.preset_catalog.available', lambda *args: True)
    app = Flask(__name__)
    app.secret_key = 'tests-only'
    app.register_blueprint(preset_editor_bp)
    return app.test_client()


def definition(name='A'):
    return {'name': name, 'video_codec': 'libx265', 'output_format': 'matroska'}


def etag(client):
    return client.get('/api/preset-editor').headers['ETag']


def test_get_preserves_four_layers_and_diagnostics(client):
    result = client.get('/api/preset-editor')
    assert result.status_code == 200
    payload = result.get_json()
    assert set(payload['layers']) == {'video', 'audio', 'subtitles', 'data'}
    assert payload['defaults']['video'] == 'hevc_auto_level_rip'
    assert payload['selections']['video'] is None
    assert payload['storage']['ready']
    assert result.headers['ETag'] == f'"{payload["catalog_revision"]}"'


@pytest.mark.parametrize('headers,status', [({}, 428), ({'If-Match': '*'}, 412), ({'If-Match': 'W/"a"'}, 412), ({'If-Match': 'bad'}, 412)])
def test_mutation_requires_strong_match(client, headers, status):
    result = client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers)
    assert result.status_code == status


def test_crud_conflict_reset_and_defaults(client):
    initial = etag(client)
    created = client.post('/api/preset-editor/video', json={'definition': definition(), 'set_default': True}, headers={'If-Match': initial})
    assert created.status_code == 201
    payload = created.get_json()
    url = created.headers['Location']
    assert payload['id'].startswith('user_')
    conflict = client.put(url, json={'definition': definition('B')}, headers={'If-Match': initial})
    assert conflict.status_code == 412
    assert conflict.get_json()['code'] == 'catalog_revision_conflict'
    changed = client.put(url, json={'definition': definition('B')}, headers={'If-Match': etag(client)})
    assert changed.status_code == 200
    assert changed.get_json()['definition']['name'] == 'B'
    blocked = client.delete(url, json={}, headers={'If-Match': etag(client)})
    assert blocked.status_code == 409
    deleted = client.delete(url, json={'replacement_default_id': 'hevc_rip'}, headers={'If-Match': etag(client)})
    assert deleted.status_code == 200
    assert client.get(url).status_code == 404
    restored_defaults = client.put('/api/preset-editor/defaults', json={'defaults': {'video': None}}, headers={'If-Match': etag(client)})
    assert restored_defaults.get_json()['defaults']['video'] == 'hevc_auto_level_rip'
    builtin = '/api/preset-editor/video/hevc_rip'
    assert client.put(builtin, json={'definition': definition('Override')}, headers={'If-Match': etag(client)}).get_json()['source'] == 'override'
    assert client.post(builtin + '/reset', json={}, headers={'If-Match': etag(client)}).get_json()['source'] == 'builtin'


def test_origin_csrf_and_json_only(client):
    revision = etag(client)
    headers = {'If-Match': revision, 'Origin': 'https://evil.test'}
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 403
    headers['Origin'] = 'http://localhost'
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 403
    with client.session_transaction() as session:
        session['preset_editor_csrf'] = 'valid-test-token'
    headers['X-CSRF-Token'] = 'valid-test-token'
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 201
    assert client.post('/api/preset-editor/video', data='{}', headers={'If-Match': etag(client)}).status_code == 415


def test_explicit_public_origin_supports_https_proxy_with_csrf(client):
    client.application.config['PRESET_EDITOR_PUBLIC_ORIGIN'] = 'https://encoder.example:443/'
    headers = {'If-Match': etag(client), 'Origin': 'https://encoder.example'}
    with client.session_transaction() as session:
        session['preset_editor_csrf'] = 'valid-test-token'
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 403
    headers['X-CSRF-Token'] = 'wrong-token'
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 403
    headers['X-CSRF-Token'] = 'valid-test-token'
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 201
    headers.update({'If-Match': etag(client), 'Origin': 'https://other.example'})
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 403


def test_forwarded_origin_is_not_implicitly_trusted(client):
    with client.session_transaction() as session:
        session['preset_editor_csrf'] = 'valid-test-token'
    headers = {'If-Match': etag(client), 'Origin': 'https://localhost',
               'X-Forwarded-Proto': 'https', 'X-CSRF-Token': 'valid-test-token'}
    assert client.post('/api/preset-editor/video', json={'definition': definition()}, headers=headers).status_code == 403


def test_public_origin_does_not_require_proxy_upstream_host_to_be_public(client):
    client.application.config['PRESET_EDITOR_PUBLIC_ORIGIN'] = 'https://encoder.example'
    serializer = client.application.session_interface.get_signing_serializer(client.application)
    session_cookie = serializer.dumps({'preset_editor_csrf': 'valid-test-token'})
    with client.application.test_client(use_cookies=False) as proxy_client:
        result = proxy_client.post('/api/preset-editor/video', base_url='http://altt_video_encoder:5000',
                             json={'definition': definition()}, headers={'If-Match': etag(client),
                             'Origin': 'https://encoder.example', 'X-CSRF-Token': 'valid-test-token',
                             'Cookie': f'session={session_cookie}'})
    assert result.status_code == 201


@pytest.mark.parametrize('origin', ['not-an-origin', 'ftp://encoder.example', 'https://user:pass@encoder.example',
    'https://encoder.example/path', 'https://encoder.example?x=1', 'https://encoder.example#fragment',
    'https://encoder.example:invalid', 'https://encoder.example:0', 'https://encoder.example:',
    'https://encoder.example https://other.example', 'https://encoder.example\\path',
    'https://*.example', 'https://one.example,two.example', 'https://encoder..example',
    'https://encoder.example#', 'https://encoder.example?'])
def test_invalid_public_origin_is_reported_without_writes(client, origin):
    client.application.config['PRESET_EDITOR_PUBLIC_ORIGIN'] = origin
    revision = etag(client)
    result = client.post('/api/preset-editor/video', json={'definition': definition()},
                         headers={'If-Match': revision, 'Origin': 'http://localhost'})
    assert result.status_code == 503
    assert result.json['code'] == 'invalid_public_origin'
    assert etag(client) == revision


def test_duplicate_json_unknown_fields_limits_and_safe_names(client):
    assert client.post('/api/preset-editor/video', data='{"definition":{},"definition":{}}', content_type='application/json', headers={'If-Match': etag(client)}).status_code == 400
    assert client.post('/api/preset-editor/video', json={'definition': definition(), 'path': '/tmp/x'}, headers={'If-Match': etag(client)}).status_code == 400
    assert client.post('/api/preset-editor/video', json={'definition': definition(), 'set_default': 'true'}, headers={'If-Match': etag(client)}).status_code == 400
    assert client.post('/api/preset-editor/video', data=b' ' * (5 * 1024 * 1024 + 1), content_type='application/json', headers={'If-Match': etag(client)}).status_code == 413
    result = client.post('/api/preset-editor/video', json={'definition': definition('<script>alert(1)</script>')}, headers={'If-Match': etag(client)})
    assert result.status_code == 201
    assert result.get_json()['name'] == '<script>alert(1)</script>'


def test_export_preview_import_and_new_revision(client):
    created = client.post('/api/preset-editor/video', json={'definition': definition()}, headers={'If-Match': etag(client)})
    exported = client.get('/api/preset-editor/export')
    catalog = exported.get_json()
    assert exported.headers['Content-Disposition'].startswith('attachment;')
    assert 'path' not in catalog
    catalog['entries'][0]['definition']['name'] = 'Imported'
    payload = {'catalog': catalog, 'mode': 'replace_user_catalog'}
    preview = client.post('/api/preset-editor/import/preview', json=payload, headers={'If-Match': etag(client)})
    assert preview.status_code == 200
    assert client.get(created.headers['Location']).get_json()['name'] == 'A'
    payload['preview_digest'] = preview.get_json()['preview_digest']
    imported = client.post('/api/preset-editor/import', json=payload, headers={'If-Match': etag(client)})
    assert imported.status_code == 200
    assert client.get(created.headers['Location']).get_json()['name'] == 'Imported'
    assert client.get('/api/preset-editor/export').get_json()['revision'] != catalog['revision']


def test_corrupt_startup_diagnostic_has_no_etag(client, monkeypatch, tmp_path):
    path = tmp_path / 'corrupt.json'
    path.write_text('invalid')
    monkeypatch.setenv('USER_PRESETS_PATH', str(path))
    result = client.get('/api/preset-editor')
    assert result.status_code == 503
    assert result.get_json()['code'] == 'storage_corrupt'
    assert 'catalog_revision' not in result.get_json()
    assert 'ETag' not in result.headers


def test_raw_catalog_import_rejects_duplicate_keys_without_losing_original(client):
    created = client.post('/api/preset-editor/video', json={'definition': definition('Keep me')}, headers={'If-Match': etag(client)})
    before = preset_manager.service.store.path.read_bytes()
    raw = client.get('/api/preset-editor/export').get_data(as_text=True)
    duplicate = raw.replace('"schema_version":1', '"schema_version":1,"schema_version":1')
    assert duplicate != raw
    rejected = client.post('/api/preset-editor/import/preview',
        json={'catalog': duplicate, 'mode': 'replace_user_catalog'}, headers={'If-Match': etag(client)})
    assert rejected.status_code == 400
    assert 'Повторяющийся ключ' in rejected.json['error']
    assert preset_manager.service.store.path.read_bytes() == before
    accepted = client.post('/api/preset-editor/import/preview',
        json={'catalog': raw, 'mode': 'replace_user_catalog'}, headers={'If-Match': etag(client)})
    assert accepted.status_code == 200
    assert client.get(created.headers['Location']).json['name'] == 'Keep me'
