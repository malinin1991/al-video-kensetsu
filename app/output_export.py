"""Безопасное копирование готового результата в серверный каталог."""

from contextlib import ExitStack, contextmanager
import errno
import os
import shutil
import stat
import uuid

from app.internal_source import normalize_source_path
from app.logger import logger
from app.models.job import JobStatus


DEFAULT_OUTPUT_ROOTS = ('/anilibria',)
COPY_BUFFER_SIZE = 4 * 1024 * 1024


class OutputExportError(Exception):
    """Ошибка сохранения с HTTP статусом и данными для подтверждения в UI."""

    def __init__(self, message, status_code, *, code=None, path=None, filename=None):
        super().__init__(message)
        self.status_code = status_code
        self.payload = {'error': message}
        for key, value in (('code', code), ('path', path), ('filename', filename)):
            if value is not None:
                self.payload[key] = value


def get_internal_output_roots():
    """Пустой INTERNAL_OUTPUT_ROOTS отключает серверное сохранение."""
    raw = os.environ.get('INTERNAL_OUTPUT_ROOTS')
    if raw is None:
        return DEFAULT_OUTPUT_ROOTS
    return tuple(part.strip() for part in raw.split(',') if part.strip())


def _inside_root(path, root):
    try:
        return os.path.commonpath((os.path.normcase(path), os.path.normcase(root))) == os.path.normcase(root)
    except ValueError:
        return False


def resolve_output_directory(directory, *, create_directories=False):
    """Проверяет jail и при явном запросе создаёт подкаталоги существующего root."""
    if not isinstance(directory, str) or '\0' in directory:
        raise OutputExportError('Каталог должен быть строкой с абсолютным путём', 400)
    normalized = normalize_source_path(directory)
    if not normalized or not os.path.isabs(normalized):
        raise OutputExportError('Укажите абсолютный путь к каталогу', 400)

    roots = get_internal_output_roots()
    if not roots:
        raise OutputExportError('Серверное сохранение отключено', 403)
    resolved = os.path.realpath(normalized)
    allowed_roots = [os.path.realpath(root) for root in roots]
    allowed_roots = [root for root in allowed_roots if _inside_root(resolved, root)]
    if not allowed_roots:
        raise OutputExportError('Каталог вне разрешённых корней', 403)

    if create_directories:
        existing_roots = []
        for root in allowed_roots:
            try:
                root_info = os.stat(root)
            except FileNotFoundError:
                continue
            except NotADirectoryError as exc:
                raise OutputExportError('Разрешённый корень не является каталогом', 400) from exc
            if not stat.S_ISDIR(root_info.st_mode):
                raise OutputExportError('Разрешённый корень не является каталогом', 400)
            existing_roots.append(root)
        if not existing_roots:
            raise OutputExportError('Разрешённый корневой каталог не найден', 404)
        _create_output_directories(resolved, max(existing_roots, key=len))
    try:
        directory_info = os.stat(resolved)
    except FileNotFoundError as exc:
        raise OutputExportError('Каталог не найден', 404) from exc
    except NotADirectoryError as exc:
        raise OutputExportError('Указанный путь не является каталогом', 400) from exc
    if not stat.S_ISDIR(directory_info.st_mode):
        raise OutputExportError('Указанный путь не является каталогом', 400)
    return resolved


def _supports_directory_fd():
    """Windows использует проверки путей вместо недоступных dir_fd операций."""
    return (
        os.name == 'posix'
        and hasattr(os, 'O_DIRECTORY')
        and hasattr(os, 'O_NOFOLLOW')
        and all(func in os.supports_dir_fd for func in (os.open, os.stat, os.unlink, os.link, os.rename, os.mkdir))
    )


def _open_directory_fd(directory):
    """Открывает каждый компонент без переходов по symlink после проверки jail."""
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(os.path.sep, flags)
    try:
        for component in directory.split(os.path.sep):
            if not component:
                continue
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _check_directory_location(directory, info):
    """Проверяет, что путь всё ещё ведёт к открытому/проверенному каталогу."""
    current = os.stat(directory, follow_symlinks=False)
    if (
        os.path.realpath(directory) != directory
        or not stat.S_ISDIR(current.st_mode)
        or not os.path.samestat(info, current)
    ):
        raise OutputExportError('Каталог изменился; повторите сохранение', 403)


def _open_output_child(parent_descriptor, component):
    """Открывает компонент дерева без перехода по появившейся symlink."""
    try:
        return os.open(
            component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
            dir_fd=parent_descriptor,
        )
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            info = os.stat(component, dir_fd=parent_descriptor, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise OutputExportError('Каталог изменился; повторите сохранение', 403) from exc
            raise OutputExportError('Указанный путь не является каталогом', 400) from exc
        raise


def _create_output_directories(directory, root):
    """Создаёт только дочерние каталоги, не создаёт и не меняет права root."""
    relative = os.path.relpath(directory, root)
    components = [] if relative == os.curdir else relative.split(os.sep)
    if _supports_directory_fd():
        descriptor = _open_directory_fd(root)
        current_path = root
        try:
            for component in components:
                _check_directory_location(current_path, os.fstat(descriptor))
                try:
                    child_descriptor = _open_output_child(descriptor, component)
                except FileNotFoundError:
                    # mkdir не следует по symlink. Если другой запрос успел
                    # создать компонент, последующий O_NOFOLLOW проверит его.
                    try:
                        os.mkdir(component, mode=0o777, dir_fd=descriptor)
                    except FileExistsError:
                        pass
                    child_descriptor = _open_output_child(descriptor, component)
                os.close(descriptor)
                descriptor = child_descriptor
                current_path = os.path.join(current_path, component)
                _check_directory_location(current_path, os.fstat(descriptor))
        finally:
            os.close(descriptor)
        return

    # На Windows dir_fd недоступен: перед каждым mkdir проверяем родителя,
    # после mkdir — его неизменность и тип созданного компонента.
    current_path = root
    parent_info = os.stat(root, follow_symlinks=False)
    for component in components:
        _check_directory_location(current_path, parent_info)
        child_path = os.path.join(current_path, component)
        try:
            child_info = os.stat(child_path, follow_symlinks=False)
        except FileNotFoundError:
            try:
                os.mkdir(child_path, mode=0o777)
            except FileExistsError:
                pass
            child_info = os.stat(child_path, follow_symlinks=False)
        _check_directory_location(current_path, parent_info)
        if stat.S_ISLNK(child_info.st_mode):
            raise OutputExportError('Каталог изменился; повторите сохранение', 403)
        if not stat.S_ISDIR(child_info.st_mode):
            raise OutputExportError('Указанный путь не является каталогом', 400)
        _check_directory_location(child_path, child_info)
        current_path, parent_info = child_path, child_info


class _Destination:
    """Все операции привязаны к каталогу, а не к заменяемому пути к нему."""

    def __init__(self, directory):
        self.directory = directory
        self.descriptor = None
        self.info = os.stat(directory, follow_symlinks=False)
        if _supports_directory_fd():
            try:
                self.descriptor = _open_directory_fd(directory)
            except OSError as exc:
                if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                    raise OutputExportError('Каталог изменился; повторите сохранение', 403) from exc
                raise
            opened = os.fstat(self.descriptor)
            if not os.path.samestat(self.info, opened):
                os.close(self.descriptor)
                self.descriptor = None
                raise OutputExportError('Каталог изменился; повторите сохранение', 403)

    def close(self):
        if self.descriptor is not None:
            os.close(self.descriptor)

    def _check_location(self):
        _check_directory_location(self.directory, self.info)

    def _path(self, name):
        return name if self.descriptor is not None else os.path.join(self.directory, name)

    def target_info(self, name):
        self._check_location()
        try:
            return os.stat(self._path(name), dir_fd=self.descriptor, follow_symlinks=False)
        except FileNotFoundError:
            return None

    def create_temp(self, name):
        self._check_location()
        return os.open(
            self._path(name), os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600, dir_fd=self.descriptor,
        )

    def publish(self, temporary_name, filename, overwrite):
        self._check_location()
        source, target = self._path(temporary_name), self._path(filename)
        if overwrite:
            os.replace(source, target, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)
        else:
            # Создание ссылки атомарно и отказывает при любом занятом имени.
            # Поэтому файл, появившийся во время копирования, тоже не затирается.
            os.link(source, target, src_dir_fd=self.descriptor, dst_dir_fd=self.descriptor)

    def remove_temp(self, name):
        if self.descriptor is None:
            try:
                self._check_location()
            except (OutputExportError, OSError):
                # Без dir_fd нельзя безопасно удалить файл через подменённый путь.
                logger.main_logger.warning(
                    'Каталог изменился; временный файл нельзя безопасно удалить: %s',
                    os.path.join(self.directory, name),
                )
                return
        try:
            os.unlink(self._path(name), dir_fd=self.descriptor)
        except FileNotFoundError:
            pass


@contextmanager
def _destination(directory):
    destination = _Destination(directory)
    try:
        yield destination
    finally:
        destination.close()


def _validate_target(destination, filename, source_info, overwrite):
    info = destination.target_info(filename)
    if info is None:
        return False
    path = os.path.join(destination.directory, filename)
    if not stat.S_ISREG(info.st_mode):
        raise OutputExportError(
            'Имя назначения занято каталогом, ссылкой или другим специальным файлом',
            409, code='invalid_target', path=path, filename=filename,
        )
    if os.path.samestat(info, source_info):
        raise OutputExportError(
            'Нельзя сохранить файл поверх самого результата задания',
            409, code='same_file', path=path, filename=filename,
        )
    if not overwrite:
        raise OutputExportError(
            'Файл уже существует. Перезаписать его?',
            409, code='file_exists', path=path, filename=filename,
        )
    return True


def _copy_stream(source, destination):
    shutil.copyfileobj(source, destination, length=COPY_BUFFER_SIZE)


def save_job_output(job, directory, *, overwrite=False, create_directories=False):
    """Копирует готовый файл; Job и его исходный результат остаются прежними."""
    if not isinstance(overwrite, bool):
        raise OutputExportError('overwrite должен быть логическим значением', 400)
    if not isinstance(create_directories, bool):
        raise OutputExportError('create_directories должен быть логическим значением', 400)
    if job.status != JobStatus.DONE:
        raise OutputExportError('Файл ещё не готов', 400)
    if not job.output_path:
        raise OutputExportError('Выходной файл не найден', 404)

    filename = job.get_download_filename()
    if (
        not isinstance(filename, str) or not filename or filename in ('.', '..')
        or '\0' in filename or '/' in filename or '\\' in filename
    ):
        raise OutputExportError('Недопустимое имя выходного файла', 400)

    temporary_name = f'.kensetsu-export-{uuid.uuid4().hex}.tmp'
    try:
        # O_NONBLOCK не даёт зависнуть на неожиданном FIFO вместо результата.
        source_fd = os.open(job.output_path, os.O_RDONLY | getattr(os, 'O_NONBLOCK', 0))
        with ExitStack() as stack:
            source = stack.enter_context(os.fdopen(source_fd, 'rb'))
            source_info = os.fstat(source.fileno())
            if not stat.S_ISREG(source_info.st_mode):
                raise OutputExportError('Выходной файл не найден', 404)
            directory = resolve_output_directory(directory, create_directories=create_directories)
            destination = stack.enter_context(_destination(directory))
            _validate_target(destination, filename, source_info, overwrite)
            temporary_created = False
            try:
                temporary_fd = destination.create_temp(temporary_name)
                temporary_created = True
                with os.fdopen(temporary_fd, 'wb') as temporary:
                    _copy_stream(source, temporary)
                    if hasattr(os, 'fchmod'):
                        # Копия сохраняет rw-права результата, чтобы root в Docker
                        # не оставлял файл 0600, недоступный медиасервису.
                        os.fchmod(temporary.fileno(), source_info.st_mode & 0o666)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                overwritten = _validate_target(destination, filename, source_info, overwrite)
                try:
                    destination.publish(temporary_name, filename, overwrite)
                except FileExistsError as exc:
                    raise OutputExportError(
                        'Файл уже существует. Перезаписать его?',
                        409, code='file_exists', path=os.path.join(directory, filename), filename=filename,
                    ) from exc
            finally:
                if temporary_created:
                    destination.remove_temp(temporary_name)
    except OutputExportError:
        raise
    except PermissionError as exc:
        raise OutputExportError('Недостаточно прав для сохранения файла', 403) from exc
    except (FileNotFoundError, NotADirectoryError, IsADirectoryError) as exc:
        raise OutputExportError('Выходной файл или каталог не найден', 404) from exc
    except OSError as exc:
        logger.main_logger.error('Не удалось сохранить результат задания %s: %s', job.id, exc)
        raise OutputExportError('Не удалось сохранить файл на сервере', 500) from exc

    path = os.path.join(directory, filename)
    logger.main_logger.info('Результат задания %s сохранён: %s', job.id, path)
    return {'saved': True, 'path': path, 'filename': filename, 'overwritten': overwritten}
