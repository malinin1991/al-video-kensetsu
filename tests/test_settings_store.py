"""Тесты персистентного хранения ника."""

from __future__ import annotations

import os

import pytest

import app.settings_store as settings_store


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    path = tmp_path / 'app_settings.json'
    monkeypatch.setenv('APP_SETTINGS_PATH', str(path))
    monkeypatch.delenv('METADATA_NICKNAME', raising=False)
    return path


class TestSettingsStore:
    def test_env_fallback_when_no_file(self, settings_file, monkeypatch):
        monkeypatch.setenv('METADATA_NICKNAME', 'FromEnv')
        assert not settings_file.exists()
        assert settings_store.get_metadata_nickname() == 'FromEnv'
        assert settings_store.has_saved_nickname() is False

    def test_saved_empty_overrides_env(self, settings_file, monkeypatch):
        monkeypatch.setenv('METADATA_NICKNAME', 'FromEnv')
        settings_store.save_settings({'metadata_nickname': ''})
        assert settings_store.has_saved_nickname() is True
        assert settings_store.get_metadata_nickname() == ''
        assert settings_file.exists()

    def test_saved_nickname_persists(self, settings_file):
        settings_store.save_settings({'metadata_nickname': 'GeeKaZ0iD'})
        assert settings_store.get_metadata_nickname() == 'GeeKaZ0iD'
        # повторная загрузка
        assert settings_store.load_settings()['metadata_nickname'] == 'GeeKaZ0iD'

    def test_atomic_write(self, settings_file):
        settings_store.save_settings({'metadata_nickname': 'A'})
        settings_store.save_settings({'metadata_nickname': 'B'})
        assert settings_file.read_text(encoding='utf-8')
        assert settings_store.get_metadata_nickname() == 'B'
        assert not os.path.exists(str(settings_file) + '.tmp')

    def test_max_concurrent_default_is_one(self, settings_file, monkeypatch):
        monkeypatch.delenv('MAX_CONCURRENT_ENCODES', raising=False)
        assert not settings_file.exists()
        assert settings_store.get_max_concurrent_encodes() == 1
        assert settings_store.has_saved_max_concurrent() is False

    def test_max_concurrent_env_fallback(self, settings_file, monkeypatch):
        monkeypatch.setenv('MAX_CONCURRENT_ENCODES', '3')
        assert settings_store.get_max_concurrent_encodes() == 3

    def test_max_concurrent_saved_overrides_env(self, settings_file, monkeypatch):
        monkeypatch.setenv('MAX_CONCURRENT_ENCODES', '4')
        settings_store.save_settings({'max_concurrent_encodes': 2})
        assert settings_store.has_saved_max_concurrent() is True
        assert settings_store.get_max_concurrent_encodes() == 2
        assert '"max_concurrent_encodes": 2' in settings_file.read_text(
            encoding='utf-8'
        )

    def test_max_concurrent_rejects_out_of_range(self, settings_file):
        with pytest.raises(ValueError):
            settings_store.save_settings({'max_concurrent_encodes': 0})
        with pytest.raises(ValueError):
            settings_store.save_settings({'max_concurrent_encodes': 99})

    def test_save_preserves_other_keys(self, settings_file):
        settings_store.save_settings({'metadata_nickname': 'Nick'})
        settings_store.save_settings({'max_concurrent_encodes': 2})
        data = settings_store.load_settings()
        assert data['metadata_nickname'] == 'Nick'
        assert data['max_concurrent_encodes'] == 2

    @pytest.mark.parametrize('value', [True, False, 1.9, 2.0, '1.5', None, [], {}])
    def test_max_concurrent_rejects_non_integer_without_overwriting(self, settings_file, value):
        settings_store.save_settings({'max_concurrent_encodes': 3})
        before = settings_file.read_bytes()
        with pytest.raises(ValueError):
            settings_store.save_settings({'max_concurrent_encodes': value})
        assert settings_file.read_bytes() == before

    @pytest.mark.parametrize('value', [1, 16, '2', ' 3 '])
    def test_max_concurrent_accepts_integer_and_form_values(self, settings_file, value):
        settings_store.save_settings({'max_concurrent_encodes': value})
        assert settings_store.get_max_concurrent_encodes() == int(value)

    def test_legacy_logs_migration(self, tmp_path, monkeypatch):
        """Читает ник из старого /app/logs, если нового файла ещё нет."""
        new_path = tmp_path / 'settings' / 'app_settings.json'
        legacy_dir = tmp_path / 'logs'
        legacy_dir.mkdir()
        legacy_path = legacy_dir / 'app_settings.json'
        legacy_path.write_text(
            '{"metadata_nickname": "LegacyNick"}\n',
            encoding='utf-8',
        )
        monkeypatch.setenv('APP_SETTINGS_PATH', str(new_path))
        monkeypatch.setattr(
            settings_store,
            '_LEGACY_SETTINGS_PATHS',
            (str(legacy_path),),
        )
        monkeypatch.delenv('METADATA_NICKNAME', raising=False)
        # APP_SETTINGS_PATH задан — миграция legacy отключена по коду;
        # проверяем чтение через пустой APP_SETTINGS_PATH + подмену default
        monkeypatch.delenv('APP_SETTINGS_PATH', raising=False)
        monkeypatch.setattr(
            settings_store,
            '_DEFAULT_SETTINGS_PATH',
            str(new_path),
        )
        assert settings_store.get_metadata_nickname() == 'LegacyNick'
        settings_store.save_settings({'metadata_nickname': 'LegacyNick'})
        assert new_path.exists()
        assert settings_store.get_metadata_nickname() == 'LegacyNick'
