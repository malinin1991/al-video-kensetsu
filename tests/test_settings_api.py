"""API настроек метаданных и приложения."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.main import app
import app.routes as routes
import app.settings_store as settings_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / 'app_settings.json'
    monkeypatch.setenv('APP_SETTINGS_PATH', str(path))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)
    monkeypatch.delenv('MAX_CONCURRENT_ENCODES', raising=False)
    dummy = MagicMock()
    dummy.max_concurrent = 1
    dummy.set_max_concurrent.side_effect = lambda v: v
    monkeypatch.setattr(routes, 'get_ffmpeg_worker_instance', lambda: dummy)
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client, dummy


class TestMetadataSettingsApi:
    def test_get_default_empty(self, client):
        test_client, _ = client
        response = test_client.get('/api/settings/metadata')
        assert response.status_code == 200
        data = response.get_json()
        assert data['metadata_nickname'] == ''
        assert data['nickname_from_file'] is False

    def test_put_and_get(self, client):
        test_client, _ = client
        response = test_client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'GeeKaZ0iD'},
        )
        assert response.status_code == 200
        assert response.get_json()['metadata_nickname'] == 'GeeKaZ0iD'

        response = test_client.get('/api/settings/metadata')
        data = response.get_json()
        assert data['metadata_nickname'] == 'GeeKaZ0iD'
        assert data['nickname_from_file'] is True

    def test_put_empty_keeps_file_priority(self, client, monkeypatch):
        test_client, _ = client
        monkeypatch.setenv('METADATA_NICKNAME', 'FromEnv')
        test_client.put('/api/settings/metadata', json={'metadata_nickname': ''})
        data = test_client.get('/api/settings/metadata').get_json()
        assert data['metadata_nickname'] == ''
        assert data['nickname_from_file'] is True

    def test_put_rejects_invalid(self, client):
        test_client, _ = client
        response = test_client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'bad\nnick'},
        )
        assert response.status_code == 400
        assert 'error' in response.get_json()

    def test_put_allows_specials(self, client):
        test_client, _ = client
        response = test_client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'Nick@AL#1'},
        )
        assert response.status_code == 200
        assert response.get_json()['metadata_nickname'] == 'Nick@AL#1'

    def test_put_allows_brackets_and_plus(self, client):
        test_client, _ = client
        response = test_client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'Ник [AL]+1'},
        )
        assert response.status_code == 200
        assert response.get_json()['metadata_nickname'] == 'Ник [AL]+1'


class TestAppSettingsApi:
    def test_get_default_concurrent_is_one(self, client):
        test_client, _ = client
        response = test_client.get('/api/settings')
        assert response.status_code == 200
        data = response.get_json()
        assert data['max_concurrent_encodes'] == 1
        assert data['default_concurrent_encodes'] == 1
        assert data['max_concurrent_encodes_from_file'] is False
        assert data['min_concurrent_encodes'] == 1
        assert data['max_concurrent_encodes_limit'] == 16

    def test_put_updates_and_applies_to_worker(self, client):
        test_client, dummy = client
        response = test_client.put(
            '/api/settings',
            json={'max_concurrent_encodes': 3},
        )
        assert response.status_code == 200
        data = response.get_json()
        assert data['max_concurrent_encodes'] == 3
        assert data['max_concurrent_encodes_from_file'] is True
        dummy.set_max_concurrent.assert_called_once_with(3)
        assert settings_store.get_max_concurrent_encodes() == 3

        again = test_client.get('/api/settings').get_json()
        assert again['max_concurrent_encodes'] == 3

    def test_put_rejects_out_of_range(self, client):
        test_client, dummy = client
        response = test_client.put(
            '/api/settings',
            json={'max_concurrent_encodes': 0},
        )
        assert response.status_code == 400
        dummy.set_max_concurrent.assert_not_called()

        response = test_client.put(
            '/api/settings',
            json={'max_concurrent_encodes': 99},
        )
        assert response.status_code == 400

    def test_settings_page_renders(self, client):
        test_client, _ = client
        response = test_client.get('/settings')
        assert response.status_code == 200
        assert 'Макс. одновременных кодирований'.encode('utf-8') in response.data
