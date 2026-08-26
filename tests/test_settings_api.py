"""API настроек метаданных."""

from __future__ import annotations

import pytest

from app.main import app
import app.settings_store as settings_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    path = tmp_path / 'app_settings.json'
    monkeypatch.setenv('APP_SETTINGS_PATH', str(path))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)
    app.config['TESTING'] = True
    with app.test_client() as test_client:
        yield test_client


class TestMetadataSettingsApi:
    def test_get_default_empty(self, client):
        response = client.get('/api/settings/metadata')
        assert response.status_code == 200
        data = response.get_json()
        assert data['metadata_nickname'] == ''
        assert data['nickname_from_file'] is False

    def test_put_and_get(self, client):
        response = client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'GeeKaZ0iD'},
        )
        assert response.status_code == 200
        assert response.get_json()['metadata_nickname'] == 'GeeKaZ0iD'

        response = client.get('/api/settings/metadata')
        data = response.get_json()
        assert data['metadata_nickname'] == 'GeeKaZ0iD'
        assert data['nickname_from_file'] is True

    def test_put_empty_keeps_file_priority(self, client, monkeypatch):
        monkeypatch.setenv('METADATA_NICKNAME', 'FromEnv')
        client.put('/api/settings/metadata', json={'metadata_nickname': ''})
        data = client.get('/api/settings/metadata').get_json()
        assert data['metadata_nickname'] == ''
        assert data['nickname_from_file'] is True

    def test_put_rejects_invalid(self, client):
        response = client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'bad\nnick'},
        )
        assert response.status_code == 400
        assert 'error' in response.get_json()

    def test_put_allows_specials(self, client):
        response = client.put(
            '/api/settings/metadata',
            json={'metadata_nickname': 'Nick@AL#1'},
        )
        assert response.status_code == 200
        assert response.get_json()['metadata_nickname'] == 'Nick@AL#1'
