"""Package sanitized workspace copies for the private Hugging Face Docker Space."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import shutil
import sys
import tarfile
import tempfile
from urllib.parse import urlsplit

import lancedb
import pandas as pd
from pydantic import BaseModel
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ir_graphrag.engine import PROVIDERS, fingerprint, load_settings
from scripts.resume_embeddings import FIELDS, validate_vectors

SPACE_WORKSPACES = Path('/home/user/app/workspaces')
TABLES = ('documents', 'text_units', 'entities', 'relationships', 'communities', 'community_reports')
EXCLUDED = {'.DS_Store', 'logs', 'cache', 'backups'}


def selected_files(root: Path, graph_ready: bool):
    """The archive contains only application data, never runtime credentials/cache."""
    candidates = [root / name for name in ('settings.yaml', 'manifest.json', 'ingestion-report.json',
                                           'input/documents.jsonl')]
    candidates += list((root / 'prompts').glob('*.txt')) + list((root / 'answers').glob('*.json'))
    if graph_ready:
        candidates += list((root / 'output').rglob('*'))
    for path in sorted(candidates):
        relative = path.relative_to(root)
        if any(part in EXCLUDED or part.startswith('.env') for part in relative.parts):
            continue
        if path.suffix.lower() in {'.log', '.sqlite', '.sqlite3', '.db'}:
            continue
        if any(root.joinpath(*relative.parts[:depth]).is_symlink() for depth in range(1, len(relative.parts) + 1)):
            continue
        if path.is_file():
            yield path


def cloud_settings(settings: dict, target: Path, *, provider='openai', api_base=None, model=None):
    if provider not in {'openai', 'deepseek', 'zai', 'openclaw'}:
        raise ValueError('Space provider must be openai, deepseek, zai, or openclaw.')
    chat = PROVIDERS[provider]
    endpoint = (api_base or chat['api_base']).rstrip('/')
    if provider == 'openclaw':
        url = urlsplit(endpoint)
        if (not api_base or url.scheme != 'https' or not url.hostname or url.username or url.password
                or url.query or url.fragment or url.hostname == 'localhost' or url.hostname.endswith('.local')):
            raise ValueError('Remote OpenClaw requires a public HTTPS --api-base without credentials.')
        try:
            address = ipaddress.ip_address(url.hostname)
        except ValueError:
            address = None
        if address is not None and not address.is_global:
            raise ValueError('The Space cannot use a local/private OpenClaw address.')
    elif api_base:
        raise ValueError('--api-base is supported only for a public OpenClaw gateway.')
    settings = json.loads(json.dumps(settings))
    embedding_spec = PROVIDERS['openclaw' if provider == 'openclaw' else 'openai']
    vector_store = settings.get('vector_store', {})
    if vector_store.get('type', 'lancedb') != 'lancedb':
        raise ValueError('Space packaging requires a local LanceDB vector store.')
    if vector_store.get('vector_size') != 3072:
        raise ValueError('Existing vectors must use 3072-dimensional text-embedding-3-large.')
    for current in settings.get('embedding_models', {}).values():
        effective = current.get('call_args', {}).get('extra_headers', {}).get('x-openclaw-model', current.get('model'))
        if effective not in {'text-embedding-3-large', 'openai/text-embedding-3-large'}:
            raise ValueError('Existing embedding model is not equivalent to OpenAI text-embedding-3-large.')
    for group in ('completion_models', 'embedding_models'):
        completion = group == 'completion_models'
        spec = chat if completion else embedding_spec
        for name, previous in settings[group].items():
            # Rebuild transport settings so local keys and custom auth headers cannot leak.
            current = dict(model_provider='openai', auth_method='api_key',
                           api_key='${' + spec['api_key_env'] + '}',
                           api_base=endpoint if completion or provider == 'openclaw' else spec['api_base'],
                           type='ir_json_chat' if completion and provider in {'deepseek', 'zai'} else 'litellm',
                           model=(model or chat['model']) if completion else spec['embedding_model'],
                           call_args={'timeout': previous.get('call_args', {}).get('timeout', 180)})
            if 'retry' in previous:
                current['retry'] = previous['retry']
            if not completion and provider == 'openclaw':
                current['model'] = chat['model']
                current['call_args']['extra_headers'] = {'x-openclaw-model': spec['embedding_model']}
            settings[group][name] = current
    embedding_endpoint = endpoint if provider == 'openclaw' else embedding_spec['api_base']
    namespace = hashlib.sha256(json.dumps([embedding_endpoint, embedding_spec['embedding_model'], 3072]).encode()).hexdigest()[:16]
    settings['embed_text']['model_instance_name'] = f'text_embedding_{namespace}'
    for section, directory in [('input_storage', 'input'), ('output_storage', 'output'),
                               ('update_output_storage', 'update_output'), ('reporting', 'logs')]:
        settings[section] = {'type': 'file', 'base_dir': str(target / directory)}
    settings['cache'] = {'type': 'json', 'storage': {'type': 'file', 'base_dir': str(target / 'cache')}}
    # Keep only local LanceDB fields; remote-store credentials must not enter the archive.
    settings['vector_store'] = {key: value for key, value in vector_store.items()
                                if key in {'vector_size', 'index_schema'}}
    settings['vector_store'].update(type='lancedb', db_uri=str(target / 'output/lancedb'))
    for section in settings.values():
        if isinstance(section, dict):
            for field, value in section.items():
                if field.endswith('prompt') and isinstance(value, str):
                    section[field] = str(target / 'prompts' / Path(value).name)
    return settings


@contextmanager
def offline_credentials():
    names = ('OPENAI_API_KEY', 'DEEPSEEK_API_KEY', 'ZAI_API_KEY', 'GRAPHRAG_API_KEY')
    previous = {name: os.environ.get(name) for name in names}
    try:
        os.environ.update({name: 'offline-packaging-placeholder' for name in names})
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def validate_outputs(root: Path, config, stamp: dict):
    output = root / 'output'
    tables = {name: pd.read_parquet(output / f'{name}.parquet', columns=['id']) for name in TABLES}
    counts = {name: len(table) for name, table in tables.items()}
    if not all(counts.values()) or counts != stamp.get('counts'):
        raise ValueError(f'{root.name}: graph table counts do not match index-ready.json.')
    expected_documents = [json.loads(line)['id'] for line in (root / 'input/documents.jsonl').read_text().splitlines()]
    if set(tables['documents']['id']) != set(expected_documents) or counts['documents'] != len(expected_documents):
        raise ValueError(f'{root.name}: graph documents do not match the corpus.')
    db = lancedb.connect(str(output / 'lancedb'))
    for field, source in FIELDS.items():
        schema = config.vector_store.index_schema[field]
        validate_vectors(db.open_table(schema.index_name).to_arrow(), tables[source]['id'].tolist(),
                         config.vector_store.vector_size, schema.id_field, schema.vector_field)


def original_paths(config, source: Path, original_root: Path):
    """Restore the paths included in a relocated backup's original fingerprint."""
    def rebase(value):
        if isinstance(value, BaseModel):
            return value.model_copy(update={key: item if key in {'api_key', 'connection_string'} else rebase(item)
                                           for key, item in value})
        if isinstance(value, dict):
            return {key: item if key in {'api_key', 'connection_string'} else rebase(item)
                    for key, item in value.items()}
        if isinstance(value, list):
            return [rebase(item) for item in value]
        if isinstance(value, str) and value.startswith(str(source) + '/'):
            return str(original_root / Path(value).relative_to(source))
        return value
    return rebase(config)


def stage_workspace(source: Path, destination: Path, *, provider='openai', api_base=None, model=None,
                    original_root: Path | None = None):
    source, destination = source.resolve(), destination.resolve()
    for relative in ('settings.yaml', 'manifest.json', 'input/documents.jsonl'):
        path = source / relative
        if not path.is_file() or path.is_symlink() or path.parent.is_symlink():
            raise ValueError(f'{source.name}: missing regular workspace file {relative}.')
    stamp_path = source / 'index-ready.json'
    if stamp_path.is_symlink():
        raise ValueError(f'{source.name}: index-ready.json cannot be a symlink.')
    stamp = json.loads(stamp_path.read_text()) if stamp_path.is_file() else None
    with offline_credentials():
        source_config = load_settings(source)
    if original_root is not None:
        source_config = original_paths(source_config, source.resolve(), original_root.resolve())
    before = fingerprint(source, source_config)
    if stamp and before != stamp['fingerprint']:
        raise ValueError(f'{source.name}: stale index; rebuild before deployment.')
    settings = yaml.safe_load((source / 'settings.yaml').read_text())
    converted = cloud_settings(settings, SPACE_WORKSPACES / destination.name,
                               provider=provider, api_base=api_base, model=model)
    for path in selected_files(source, stamp is not None):
        target = destination / path.relative_to(source)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    # Validate the copied corpus, catching source changes and excluded prompt symlinks.
    if fingerprint(destination, source_config) != before:
        raise ValueError(f'{source.name}: source changed or a required prompt was excluded while staging.')
    if stamp:
        validate_outputs(destination, source_config, stamp)
    (destination / 'settings.yaml').write_text(yaml.safe_dump(converted, sort_keys=False), encoding='utf-8')
    with offline_credentials():
        config = load_settings(destination)
    # macOS resolves /home through a host symlink that does not exist in the Linux image.
    config = original_paths(config, SPACE_WORKSPACES.resolve(), SPACE_WORKSPACES)
    if stamp:
        relocated = dict(stamp, source_fingerprint=stamp['fingerprint'], fingerprint=fingerprint(destination, config),
                         deployment={'provider': provider, 'target_root': str(SPACE_WORKSPACES / destination.name),
                                     'source_completion_models': {name: {'model': item['model'], 'type': item.get('type', 'litellm')}
                                                                  for name, item in settings['completion_models'].items()},
                                     'packaged_at': datetime.now(timezone.utc).isoformat()})
        (destination / 'index-ready.json').write_text(json.dumps(relocated, indent=2) + '\n', encoding='utf-8')
    return {'name': destination.name, 'graph_ready': stamp is not None}


def package_workspaces(source: Path, output: Path, *, provider='openai', api_base=None, model=None, snapshots=()):
    source, output = source.resolve(), output.expanduser().resolve()
    workspaces = sorted(path for path in source.iterdir() if path.is_dir() and not path.is_symlink()
                        and (path / 'settings.yaml').is_file())
    if not workspaces:
        raise ValueError('No workspaces found.')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='space-workspaces-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'workspaces'
        results = [stage_workspace(path, stage / path.name, provider=provider, api_base=api_base, model=model)
                   for path in workspaces]
        for name, snapshot, original_root in snapshots:
            if not name or Path(name).name != name or name in {'.', '..'} or (stage / name).exists():
                raise ValueError('Snapshot name must be a unique workspace directory name.')
            snapshot = Path(snapshot).resolve()
            if not (snapshot / 'index-ready.json').is_file():
                raise ValueError(f'{name}: snapshot must have an index-ready.json marker.')
            results.append(stage_workspace(snapshot, stage / name, provider=provider, api_base=api_base,
                                           model=model, original_root=Path(original_root)))
        archive = Path(temporary) / 'workspaces.tar.gz'
        with tarfile.open(archive, 'w:gz') as bundle:
            bundle.add(stage, arcname='workspaces')
        archive.replace(output)
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--provider', choices=('openai', 'deepseek', 'zai', 'openclaw'), default='openai')
    parser.add_argument('--api-base', help='Public HTTPS OpenClaw gateway endpoint.')
    parser.add_argument('--model', help='Completion model override; embeddings remain text-embedding-3-large.')
    parser.add_argument('--snapshot', nargs=3, action='append', default=[], metavar=('NAME', 'SOURCE', 'ORIGINAL_ROOT'),
                        help='Include a verified graph backup under a separate workspace name.')
    args = parser.parse_args()
    result = package_workspaces(Path(__file__).resolve().parents[1] / 'workspaces', args.output,
                                provider=args.provider, api_base=args.api_base, model=args.model, snapshots=args.snapshot)
    print(json.dumps({'output': str(args.output), 'workspaces': result}, ensure_ascii=False, indent=2))
