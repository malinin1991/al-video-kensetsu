"""Offline preview/apply старых YAML и восстановление backup без app.main."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys
import uuid

from app.preset_catalog import PresetCatalog, load_yaml_catalog
from app.preset_schema import CatalogError, LAYERS, canonical, empty_state, fingerprint, normalize_state, parse_json
from app.preset_store import atomic_write


def migration_candidate(service, source):
    definitions, defaults = load_yaml_catalog(source, collect_errors=True)
    source_digest = fingerprint({'definitions': definitions, 'defaults': defaults})
    catalog = empty_state()
    catalog['defaults'] = defaults
    for layer in LAYERS:
        for preset_id, definition in definitions[layer].items():
            catalog['entries'].append({'layer': layer, 'id': preset_id,
                'kind': 'override' if preset_id in service._builtin[layer] else 'custom',
                'definition': definition, 'base_snapshot': None, 'base_fingerprint': None})
    catalog['migrations'] = [{'digest': source_digest, 'result': 'legacy_yaml_imported'}]
    return normalize_state(catalog), source_digest


def preview(service, source):
    service.snapshot()
    catalog, source_digest = migration_candidate(service, source)
    already = any(m['digest'] == source_digest for m in service.export()['migrations'])
    report = service.preview_import(catalog, 'merge', True)
    report['source_digest'] = source_digest
    report['sources'] = [str(path.relative_to(source)) for layer in LAYERS
                         for path in sorted((Path(source) / layer).glob('*'))
                         if path.suffix.lower() in ('.yaml', '.yml')]
    report['already_applied'] = already
    # Include source identity as well as normalized full candidate and target.
    report['preview_digest'] = fingerprint({'source_digest': source_digest, 'import_digest': report['preview_digest']})
    return report


def apply(service, source, preview_digest, if_match):
    report = preview(service, source)
    if report['already_applied']:
        return {'already_applied': True, 'catalog_revision': service.snapshot().catalog_revision}
    service._check_match(if_match)
    if preview_digest != report['preview_digest']:
        raise CatalogError('Источник или предварительный просмотр изменён; повторите preview', 'preview_conflict', 409)
    if report['conflicts']:
        raise CatalogError('Пользовательский каталог содержит конфликты; используйте import preview/merge/replace', 'import_conflict', 409, {'conflicts': report['conflicts']})
    catalog, source_digest = migration_candidate(service, source)
    if source_digest != report['source_digest']:
        raise CatalogError('Источник изменился после предварительной проверки; повторите preview', 'preview_conflict', 409)
    archive = service.store.path.parent / 'legacy_archives' / str(uuid.uuid4())
    # Archive exact bytes before committing and verify source has not changed.
    for relative in report['sources']:
        path = Path(source) / relative
        atomic_write(archive / relative, path.read_bytes())
    atomic_write(archive / 'report.json', canonical(report) + b'\n')
    archived_catalog, archived_digest = migration_candidate(service, archive)
    if report['source_digest'] != archived_digest or report['source_digest'] != migration_candidate(service, source)[1]:
        raise CatalogError('Источник изменился при архивировании; повторите preview', 'preview_conflict', 409)
    import_report = service.preview_import(archived_catalog, 'merge', True, if_match)
    result = service.import_catalog(archived_catalog, 'merge', import_report['preview_digest'], if_match, True)
    return {'catalog_revision': result['catalog_revision'], 'source_digest': source_digest,
            'archive': str(archive), 'already_applied': False}


def restore(service, backup, target_digest):
    target = service.store.path
    try:
        original = target.read_bytes()
    except OSError as exc:
        raise CatalogError(f'Не удалось прочитать восстанавливаемый файл: {exc}', 'storage_unavailable', 503) from exc
    if hashlib.sha256(original).hexdigest() != target_digest:
        raise CatalogError('SHA256 текущего файла отличается; восстановление отменено', 'restore_conflict', 409)
    try:
        restored = normalize_state(parse_json(Path(backup).read_bytes()))
    except OSError as exc:
        raise CatalogError(f'Не удалось прочитать backup: {exc}', 'storage_unavailable', 503) from exc
    builtins, defaults = load_yaml_catalog(service.builtin_dir)
    restored['revision'] = str(uuid.uuid4())
    snapshot = service._build(restored, builtins, defaults)
    preserved = target.parent / 'recovery' / f'{uuid.uuid4()}.original'
    atomic_write(preserved, original)
    if hashlib.sha256(target.read_bytes()).hexdigest() != target_digest:
        raise CatalogError('Файл изменён во время восстановления', 'restore_conflict', 409)
    atomic_write(target, canonical(restored) + b'\n')
    return {'catalog_revision': snapshot.catalog_revision, 'revision': restored['revision'], 'preserved_original': str(preserved)}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Offline перенос старых YAML и восстановление пользовательских пресетов. Сервер должен быть остановлен.')
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('preview', 'apply'):
        sub = commands.add_parser(command)
        sub.add_argument('--source', required=True, type=Path)
        if command == 'apply':
            sub.add_argument('--preview-digest', required=True)
            sub.add_argument('--if-match', required=True)
    sub = commands.add_parser('restore')
    sub.add_argument('--backup', required=True, type=Path)
    sub.add_argument('--target-digest', required=True)
    args = parser.parse_args(argv)
    try:
        service = PresetCatalog()
        if args.command == 'preview':
            result = preview(service, args.source)
        elif args.command == 'apply':
            result = apply(service, args.source, args.preview_digest, args.if_match)
        else:
            result = restore(service, args.backup, args.target_digest)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (CatalogError, OSError) as exc:
        payload = exc.to_dict() if isinstance(exc, CatalogError) else {'error': str(exc), 'code': 'storage_unavailable'}
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
