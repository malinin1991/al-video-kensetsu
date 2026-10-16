"""Изолированные SQLite и outputs для импорта приложения и каждого теста."""

import os
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest


_startup_storage = TemporaryDirectory(prefix='kensetsu-tests-')
os.environ['JOB_STORE_PATH'] = str(Path(_startup_storage.name) / 'jobs.sqlite3')
os.environ['OUTPUTS_DIR'] = str(Path(_startup_storage.name) / 'outputs')


@pytest.fixture(autouse=True)
def isolated_job_store(tmp_path, monkeypatch):
    monkeypatch.setenv('JOB_STORE_PATH', str(tmp_path / 'jobs.sqlite3'))
    monkeypatch.setenv('OUTPUTS_DIR', str(tmp_path / 'outputs'))
