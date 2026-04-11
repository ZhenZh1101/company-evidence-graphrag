"""Finish this GraphRAG 3.2 index after graph/report generation succeeded.

Uses native token splitting, preflights the complete remaining corpus against
the local gateway's character limit, and preserves the entity vector table.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

import lancedb
import numpy as np
import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ir_graphrag.engine import fingerprint, load_settings, workspace_lock
from ir_graphrag.ingest import write_json

FIELDS = {'entity_description': 'entities', 'community_full_content': 'community_reports',
          'text_unit_text': 'text_units'}
REMAINING = ['community_full_content', 'text_unit_text']


def digest(path):
    with path.open('rb') as handle:
        return hashlib.file_digest(handle, 'sha256').hexdigest()


def validate_vectors(table, expected_ids, dimensions, id_field='id', vector_field='vector'):
    ids = table.column(id_field).to_pylist()
    if len(ids) != len(expected_ids) or len(set(ids)) != len(ids) or set(ids) != set(expected_ids):
        raise ValueError('Vector IDs do not exactly match the source table.')
    vectors = table.column(vector_field).combine_chunks()
    if vectors.null_count or vectors.type.list_size != dimensions or vectors.values.null_count:
        raise ValueError('Missing vectors or incorrect vector dimensions.')
    values = vectors.values.to_numpy().reshape(len(ids), dimensions)
    if not np.isfinite(values).all() or np.any(np.linalg.norm(values, axis=1) == 0):
        raise ValueError('Vectors must be finite and nonzero.')
    return len(ids)


def preflight(config, tables):
    from graphrag.index.operations.embed_text.run_embed_text import _prepare_embed_texts, _create_text_batches
    from graphrag_llm.embedding import create_embedding

    tokenizer = create_embedding(config.embedding_models[config.embed_text.embedding_model_id]).tokenizer
    size, cap = config.embed_text.batch_size, config.embed_text.batch_max_tokens
    flush_size = size * config.concurrent_requests
    result = {}
    for name, column in [('community_reports', 'full_content'), ('text_units', 'text')]:
        texts = tables[name][column].tolist()
        if any(not isinstance(text, str) or not text.strip() for text in texts):
            raise ValueError(f'Empty embedding input in {name}.')
        lengths, batch_lengths = [], []
        for start in range(0, len(texts), flush_size):
            snippets, _ = _prepare_embed_texts(texts[start:start + flush_size], tokenizer, cap)
            batches = _create_text_batches(snippets, tokenizer, size, cap)
            lengths.extend(map(len, snippets))
            batch_lengths.extend(sum(map(len, batch)) for batch in batches)
        if max(lengths) > 8192 or max(batch_lengths) > 8192:
            raise ValueError(f'{name} exceeds the gateway character limit after splitting.')
        result[name] = dict(rows=len(texts), snippets=len(lengths), requests=len(batch_lengths),
                            max_input_chars=max(lengths), max_batch_chars=max(batch_lengths))
    return result


def resume(root):
    import graphrag.api as api

    root = root.expanduser().resolve()
    with workspace_lock(root):
        stamp = root / 'index-ready.json'
        if stamp.exists():
            raise ValueError('An index-ready marker already exists; inspect it before resuming.')
        config = load_settings(root)
        if config.workflows or set(config.embed_text.names) != set(FIELDS):
            raise ValueError('Expected the normal complete indexing configuration.')
        output = Path(config.output_storage.base_dir)
        tables = {name: pd.read_parquet(output / f'{name}.parquet') for name in
                  ('documents', 'text_units', 'entities', 'relationships', 'communities', 'community_reports')}
        counts = {name: len(table) for name, table in tables.items()}
        if not all(counts.values()):
            raise ValueError('The completed graph/report tables must all be nonempty.')
        if set(tables['community_reports']['community']) != set(tables['communities']['community']):
            raise ValueError('Community reports are incomplete.')
        ingestion = json.loads((root / 'ingestion-report.json').read_text())
        if digest(root / 'input/documents.jsonl') != ingestion['input_sha256']:
            raise ValueError('Input changed since ingestion.')
        if set(tables['documents']['id']) != set(json.loads(line)['id'] for line in
                                                 (root / 'input/documents.jsonl').read_text().splitlines()):
            raise ValueError('Saved documents do not match current input IDs.')

        db = lancedb.connect(config.vector_store.db_uri)
        schema = config.vector_store.index_schema['entity_description']
        validate_vectors(db.open_table(schema.index_name).to_arrow(), tables['entities']['id'].tolist(),
                         config.vector_store.vector_size, schema.id_field, schema.vector_field)
        preserved = [*output.glob('*.parquet'), output / 'graph.graphml',
                     *sorted((Path(config.vector_store.db_uri) / f'{schema.index_name}.lance').rglob('*'))]
        preserved = {str(p): digest(p) for p in preserved if p.is_file()}

        backup = root / 'backups' / ('embedding-resume-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        backup.mkdir(parents=True)
        shutil.copy2(root / 'settings.yaml', backup / 'settings.yaml')
        shutil.copytree(output, backup / 'output')
        shutil.copy2(root / 'logs/indexing-engine.log', backup / 'indexing-engine.log')
        write_json(backup / 'preserved-sha256.json', preserved)
        print(json.dumps({'backup': str(backup), 'source_counts': counts}), flush=True)

        settings = yaml.safe_load((root / 'settings.yaml').read_text())
        settings['embed_text']['batch_max_tokens'] = 1200
        candidate = config.model_copy(deep=True)
        candidate.embed_text.batch_max_tokens = 1200
        checked = preflight(candidate, tables)
        write_json(backup / 'preflight.json', checked)
        print(json.dumps({'preflight': checked}), flush=True)
        (root / 'settings.yaml').write_text(yaml.safe_dump(settings, sort_keys=False), encoding='utf-8')
        config = load_settings(root)
        expected_fingerprint = fingerprint(root, config)
        run_config = config.model_copy(deep=True)
        run_config.workflows = ['generate_text_embeddings']
        run_config.embed_text.names = REMAINING.copy()
        results = asyncio.run(api.build_index(config=run_config, method='standard'))
        if [r.workflow for r in results] != ['generate_text_embeddings'] or any(r.error for r in results):
            raise RuntimeError('Embedding resume failed; existing graph and backup are retained. See logs.')

        vector_counts = {}
        for field, source in FIELDS.items():
            schema = config.vector_store.index_schema[field]
            vector_counts[field] = validate_vectors(
                db.open_table(schema.index_name).to_arrow(), tables[source]['id'].tolist(),
                config.vector_store.vector_size, schema.id_field, schema.vector_field)
        if any(not Path(path).is_file() or digest(Path(path)) != expected for path, expected in preserved.items()):
            raise RuntimeError('A preserved graph or entity-vector file changed; do not mark ready.')
        if fingerprint(root, load_settings(root)) != expected_fingerprint:
            raise RuntimeError('Input/config/prompts changed during resume; do not mark ready.')
        status = dict(fingerprint=expected_fingerprint, graphrag_version='3.2.0',
                      completed_at=datetime.now(timezone.utc).isoformat(), counts=counts,
                      vector_counts=vector_counts, recovery_backup=str(backup))
        write_json(backup / 'verification.json', status)
        write_json(root / 'index-ready.json.tmp', status)
        (root / 'index-ready.json.tmp').replace(stamp)
        print(json.dumps(status, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    resume(parser.parse_args().root)
