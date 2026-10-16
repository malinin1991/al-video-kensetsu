"""Хеш при приёме и перед encode должен совпадать для любых размеров файла."""

import hashlib

import pytest

from app.file_utils import calculate_file_hash
from app.models.job import Job


@pytest.mark.parametrize('size', [0, 1, 4095, 4096, 262143, 262144, 1048579])
def test_upload_hash_and_job_verification_match_sha256(tmp_path, size):
    path = tmp_path / 'Серия [01].mkv'
    data = (bytes(range(256)) * (size // 256 + 1))[:size]
    path.write_bytes(data)
    expected = hashlib.sha256(data).hexdigest()
    job = Job('hash-test', path.name, str(path), file_hash=expected)
    assert calculate_file_hash(str(path)) == expected
    assert job.calculate_hash() == expected
    assert job.verify_hash() is True
    assert path.read_bytes() == data
    with path.open('ab') as handle:
        handle.write(b'changed')
    assert job.verify_hash() is False


def test_job_without_source_preserves_missing_hash_contract(tmp_path):
    job = Job('missing-hash', 'missing.mkv', str(tmp_path / 'missing.mkv'))
    assert job.calculate_hash() is None
    assert job.verify_hash() is False
