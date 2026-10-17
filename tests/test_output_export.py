"""Серверное сохранение: имена, подтверждение замены и границы каталогов."""

import errno
import os
import stat

import pytest

from app.main import app
from app.models.job import Job, JobStatus
import app.output_export as output_export
import app.routes as routes


@pytest.fixture
def export_env(tmp_path, monkeypatch):
    root = tmp_path / 'library'
    destination = root / 'encoded'
    destination.mkdir(parents=True)
    outputs = tmp_path / 'outputs'
    outputs.mkdir()
    result = outputs / 'Show_[HEVC]_12345678.mkv'
    result.write_bytes(b'finished-encoded-video')
    job = Job(
        id='12345678-1234-1234-1234-123456789012', filename='Show.mp4',
        original_path=str(tmp_path / 'original.mp4'), output_path=str(result),
        status=JobStatus.DONE,
    )
    monkeypatch.setenv('INTERNAL_OUTPUT_ROOTS', str(root))
    monkeypatch.setattr(routes, 'jobs_storage', {job.id: job})
    app.config['TESTING'] = True
    with app.test_client() as client:
        yield client, job, root, destination, result


def save(export_env, **kwargs):
    client, job, _root, destination, _result = export_env
    return client.post(
        f'/api/jobs/{job.id}/save', json={'directory': str(destination), **kwargs},
    )


def assert_no_temporary(destination):
    assert not list(destination.glob('.kensetsu-export-*'))


def test_save_clean_name_preserves_hashed_job_output(export_env):
    _client, job, _root, destination, result = export_env
    original_record = job.to_record()
    response = save(export_env, directory=f'  "\'{destination}\'"  ')

    assert response.status_code == 200
    target = destination / 'Show_[HEVC].mkv'
    assert response.get_json() == {
        'saved': True, 'path': str(target), 'filename': target.name, 'overwritten': False,
    }
    assert target.read_bytes() == result.read_bytes() == b'finished-encoded-video'
    assert result.name == 'Show_[HEVC]_12345678.mkv'
    assert job.to_record() == original_record
    assert_no_temporary(destination)


def test_existing_file_requires_confirmation_then_overwrites(export_env):
    _client, job, _root, destination, result = export_env
    target = destination / job.get_download_filename()
    target.write_bytes(b'previous-result')

    conflict = save(export_env)

    assert conflict.status_code == 409
    assert conflict.get_json()['code'] == 'file_exists'
    assert conflict.get_json()['path'] == str(target)
    assert conflict.get_json()['filename'] == target.name
    assert target.read_bytes() == b'previous-result'
    assert_no_temporary(destination)

    confirmed = save(export_env, overwrite=True)

    assert confirmed.status_code == 200
    assert confirmed.get_json()['overwritten'] is True
    assert target.read_bytes() == result.read_bytes()
    assert result.exists()
    assert_no_temporary(destination)


def test_explicit_overwrite_without_existing_file_is_not_reported_as_replacement(export_env):
    response = save(export_env, overwrite=True)
    assert response.status_code == 200
    assert response.get_json()['overwritten'] is False


@pytest.mark.skipif(not hasattr(os, 'fchmod'), reason='POSIX права недоступны')
def test_saved_copy_retains_read_write_permissions_without_execute_bits(export_env):
    _client, job, _root, destination, result = export_env
    result.chmod(0o6754)
    response = save(export_env)
    assert response.status_code == 200
    assert stat.S_IMODE((destination / job.get_download_filename()).stat().st_mode) == 0o644


@pytest.mark.parametrize('value', ['true', 'false', 0, 1, None, [], {}])
def test_overwrite_must_be_boolean(export_env, value):
    response = save(export_env, overwrite=value)
    assert response.status_code == 400
    assert not list(export_env[3].iterdir())


@pytest.mark.parametrize('value', [None, 12, [], {}, '', 'relative/directory', '/invalid\0path'])
def test_invalid_directory(export_env, value):
    response = save(export_env, directory=value)
    assert response.status_code == 400


@pytest.mark.parametrize('body', ['[]', 'null', 'true', '{invalid'])
def test_json_body_must_be_object(export_env, body):
    client, job, *_rest = export_env
    response = client.post(
        f'/api/jobs/{job.id}/save', data=body, content_type='application/json',
    )
    assert response.status_code == 400


def test_form_request_is_rejected(export_env):
    client, job, _root, destination, _result = export_env
    response = client.post(f'/api/jobs/{job.id}/save', data={'directory': str(destination)})
    assert response.status_code == 400


def test_missing_job(export_env):
    client, _job, _root, destination, _result = export_env
    response = client.post('/api/jobs/missing/save', json={'directory': str(destination)})
    assert response.status_code == 404


@pytest.mark.parametrize('status', [JobStatus.PLANNED, JobStatus.IN_PROGRESS, JobStatus.ERROR, JobStatus.CANCELLED])
def test_only_done_jobs_can_be_saved(export_env, status):
    export_env[1].status = status
    response = save(export_env)
    assert response.status_code == 400
    assert not list(export_env[3].iterdir())


@pytest.mark.parametrize('missing', [True, False])
def test_output_must_exist(export_env, missing):
    if missing:
        export_env[4].unlink()
    else:
        export_env[1].output_path = None
    assert save(export_env).status_code == 404


def test_output_directory_is_not_a_finished_file(export_env):
    export_env[1].output_path = str(export_env[3])
    assert save(export_env).status_code == 404


def test_missing_destination_is_not_created(export_env):
    destination = export_env[3] / 'missing'
    response = save(export_env, directory=str(destination))
    assert response.status_code == 404
    assert not destination.exists()


def test_destination_must_be_directory(export_env):
    path = export_env[3] / 'file'
    path.write_bytes(b'existing-file')
    assert save(export_env, directory=str(path)).status_code == 400


def test_disabled_output_roots(export_env, monkeypatch):
    monkeypatch.setenv('INTERNAL_OUTPUT_ROOTS', '  ,  ')
    assert save(export_env).status_code == 403


def test_default_output_root(monkeypatch):
    monkeypatch.delenv('INTERNAL_OUTPUT_ROOTS', raising=False)
    assert output_export.get_internal_output_roots() == ('/anilibria',)


def test_multiple_output_roots(export_env, monkeypatch):
    root = export_env[2]
    monkeypatch.setenv('INTERNAL_OUTPUT_ROOTS', f'/nonexistent, {root}')
    assert save(export_env).status_code == 200


def test_parent_traversal_and_sibling_prefix_are_forbidden(export_env):
    root = export_env[2]
    sibling = root.with_name(root.name + '-other')
    sibling.mkdir()
    assert save(export_env, directory=str(root / '..' / sibling.name)).status_code == 403
    assert save(export_env, directory=str(sibling)).status_code == 403


def create_symlink_or_skip(target, link):
    try:
        link.symlink_to(target, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError):
        pytest.skip('Создание symlink недоступно')


def test_destination_symlink_cannot_escape_allowed_root(export_env):
    _client, _job, root, _destination, result = export_env
    escape = root / 'escape'
    create_symlink_or_skip(result.parent, escape)
    assert save(export_env, directory=str(escape)).status_code == 403


def test_symlink_inside_allowed_root_is_resolved(export_env):
    _client, job, root, destination, _result = export_env
    alias = root / 'alias'
    create_symlink_or_skip(destination, alias)
    response = save(export_env, directory=str(alias))
    assert response.status_code == 200
    assert response.get_json()['path'] == str(destination / job.get_download_filename())


@pytest.mark.parametrize('kind', ['symlink', 'directory'])
def test_nonregular_target_is_never_overwritten(export_env, kind):
    _client, job, _root, destination, result = export_env
    target = destination / job.get_download_filename()
    if kind == 'symlink':
        create_symlink_or_skip(result, target)
    else:
        target.mkdir()
    response = save(export_env, overwrite=True)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'invalid_target'
    assert result.read_bytes() == b'finished-encoded-video'
    assert target.is_symlink() if kind == 'symlink' else target.is_dir()
    assert_no_temporary(destination)


def test_hardlink_to_source_is_not_overwritten(export_env):
    _client, job, _root, destination, result = export_env
    target = destination / job.get_download_filename()
    try:
        os.link(result, target)
    except OSError:
        pytest.skip('Hardlink недоступен')
    response = save(export_env, overwrite=True)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'same_file'
    assert target.read_bytes() == result.read_bytes()


def test_same_source_path_is_rejected(export_env, monkeypatch):
    _client, job, _root, _destination, result = export_env
    monkeypatch.setenv('INTERNAL_OUTPUT_ROOTS', str(result.parent))
    job.get_download_filename = lambda: result.name
    response = save(export_env, directory=str(result.parent), overwrite=True)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'same_file'


def test_failed_copy_keeps_existing_file_and_removes_partial(export_env, monkeypatch):
    _client, job, _root, destination, result = export_env
    target = destination / job.get_download_filename()
    target.write_bytes(b'previous-result')

    def fail_copy(_source, temporary):
        temporary.write(b'partial-copy')
        raise OSError(errno.ENOSPC, 'disk full')

    monkeypatch.setattr(output_export, '_copy_stream', fail_copy)
    response = save(export_env, overwrite=True)
    assert response.status_code == 500
    assert target.read_bytes() == b'previous-result'
    assert result.read_bytes() == b'finished-encoded-video'
    assert_no_temporary(destination)


def test_permission_error_is_reported_without_partial(export_env, monkeypatch):
    def deny(_self, _name):
        raise PermissionError('permission denied')

    monkeypatch.setattr(output_export._Destination, 'create_temp', deny)
    assert save(export_env).status_code == 403
    assert not list(export_env[3].iterdir())


def test_file_appearing_at_publication_is_never_overwritten(export_env, monkeypatch):
    _client, job, _root, destination, _result = export_env
    target = destination / job.get_download_filename()
    publish = output_export._Destination.publish

    def race(self, temporary_name, filename, overwrite):
        target.write_bytes(b'concurrent-result')
        return publish(self, temporary_name, filename, overwrite)

    monkeypatch.setattr(output_export._Destination, 'publish', race)
    response = save(export_env)
    assert response.status_code == 409
    assert response.get_json()['code'] == 'file_exists'
    assert target.read_bytes() == b'concurrent-result'
    assert_no_temporary(destination)


def test_directory_replaced_by_symlink_during_copy_does_not_redirect_writes(export_env, monkeypatch):
    if not output_export._supports_directory_fd():
        pytest.skip('Проверка dir_fd доступна только на POSIX')
    _client, job, root, destination, result = export_env
    moved = root / 'moved'
    outside = result.parent / 'outside'
    outside.mkdir()
    copy = output_export._copy_stream

    def replace_directory(source, temporary):
        destination.rename(moved)
        create_symlink_or_skip(outside, destination)
        copy(source, temporary)

    monkeypatch.setattr(output_export, '_copy_stream', replace_directory)
    response = save(export_env)
    assert response.status_code == 403
    assert not list(outside.iterdir())
    assert not (moved / job.get_download_filename()).exists()
    assert_no_temporary(moved)


def test_path_fallback_saves_without_directory_fd(export_env, monkeypatch):
    monkeypatch.setattr(output_export, '_supports_directory_fd', lambda: False)
    assert save(export_env).status_code == 200


def test_path_fallback_rejects_directory_replacement(export_env, monkeypatch):
    _client, _job, root, destination, result = export_env
    outside = result.parent / 'outside'
    outside.mkdir()
    moved = root / 'moved'
    copy = output_export._copy_stream
    monkeypatch.setattr(output_export, '_supports_directory_fd', lambda: False)
    outside_file = None

    def replace_directory(source, temporary):
        nonlocal outside_file
        temporary_name = next(destination.glob('.kensetsu-export-*')).name
        outside_file = outside / temporary_name
        outside_file.write_bytes(b'outside-file-must-not-be-deleted')
        destination.rename(moved)
        create_symlink_or_skip(outside, destination)
        copy(source, temporary)

    monkeypatch.setattr(output_export, '_copy_stream', replace_directory)
    response = save(export_env)
    assert response.status_code == 403
    assert outside_file.read_bytes() == b'outside-file-must-not-be-deleted'
