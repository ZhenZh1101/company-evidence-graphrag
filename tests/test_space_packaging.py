import json
import os
from pathlib import Path
import shutil
import tarfile
import tempfile
import unittest
from unittest.mock import patch

import lancedb
import pandas as pd
import pyarrow as pa
import yaml

from ir_graphrag.engine import fingerprint, initialize, load_settings
from scripts import package_space_workspaces as packaging


class SpacePackagingTests(unittest.TestCase):
    def workspace(self, root, ready=True):
        initialize(root)
        (root / 'input/documents.jsonl').write_text(json.dumps({'id': 'document-1', 'text': 'Example evidence.'}) + '\n')
        (root / 'manifest.json').write_text(json.dumps({'document-1': {'title': 'Example'}}))
        (root / 'ingestion-report.json').write_text('{}')
        (root / '.env').write_text('GRAPHRAG_API_KEY=fixture-value-never-published\n')
        with packaging.offline_credentials():
            config = load_settings(root)
        if ready:
            (root / 'output').mkdir(exist_ok=True)
            for name in packaging.TABLES:
                pd.DataFrame({'id': ['document-1' if name == 'documents' else name + '-1']}).to_parquet(root / 'output' / f'{name}.parquet')
            db = lancedb.connect(str(root / 'output/lancedb'))
            for field, source in packaging.FIELDS.items():
                schema = config.vector_store.index_schema[field]
                db.create_table(schema.index_name, pa.table({
                    schema.id_field: [source + '-1'],
                    schema.vector_field: pa.array([[1.0] * 3072], pa.list_(pa.float32(), 3072)),
                }))
            stamp = dict(fingerprint=fingerprint(root, config), counts={name: 1 for name in packaging.TABLES},
                         completed_at='2026-10-08T00:00:00Z', graphrag_version='3.2.0')
            (root / 'index-ready.json').write_text(json.dumps(stamp))
        return config

    def test_archive_excludes_runtime_files_and_relocation_matches_runtime_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source = base / 'local/workspaces'
            graph, corpus = source / 'graph', source / 'corpus'
            self.workspace(graph)
            self.workspace(corpus, ready=False)
            original_settings = (graph / 'settings.yaml').read_bytes()
            original_stamp = json.loads((graph / 'index-ready.json').read_text())
            for relative in ('output/.DS_Store', 'output/.env', 'output/cache/secret.txt',
                             'output/logs/engine.log', 'output/state.sqlite', 'backups/secret.txt',
                             'answers/ignored.txt', 'input/other.jsonl'):
                path = graph / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text('excluded')
            (graph / 'answers/saved.json').write_text('{}')
            (graph / 'answers/link.json').symlink_to(graph / '.env')
            (graph / 'output/link').symlink_to(graph / '.env')
            (graph / 'output/linked-dir').symlink_to(graph / 'answers', target_is_directory=True)
            (corpus / 'output').mkdir(exist_ok=True)
            (corpus / 'output/partial.parquet').write_text('partial')
            cloud = base / 'cloud/workspaces'
            archive = base / 'bundle.tar.gz'
            with patch.object(packaging, 'SPACE_WORKSPACES', cloud):
                result = packaging.package_workspaces(source, archive)
            self.assertEqual(result, [{'name': 'corpus', 'graph_ready': False}, {'name': 'graph', 'graph_ready': True}])
            with tarfile.open(archive) as bundle:
                names = bundle.getnames()
                self.assertTrue(all(not member.issym() for member in bundle.getmembers()))
                self.assertFalse(any(any(bad in name for bad in ('.env', '.DS_Store', 'cache', 'logs', 'backups', '.sqlite', 'link', 'partial')) for name in names))
                self.assertIn('workspaces/graph/answers/saved.json', names)
                bundle.extractall(base / 'cloud', filter='data')
            with packaging.offline_credentials():
                config = load_settings(cloud / 'graph')
            stamp = json.loads((cloud / 'graph/index-ready.json').read_text())
            self.assertEqual(stamp['fingerprint'], fingerprint(cloud / 'graph', config))
            self.assertEqual(stamp['source_fingerprint'], original_stamp['fingerprint'])
            self.assertEqual(stamp['completed_at'], original_stamp['completed_at'])
            self.assertEqual(config.embedding_models['default_embedding_model'].model, 'text-embedding-3-large')
            self.assertEqual((graph / 'settings.yaml').read_bytes(), original_settings)
            self.assertEqual(json.loads((graph / 'index-ready.json').read_text()), original_stamp)
            self.assertIn('${OPENAI_API_KEY}', (cloud / 'graph/settings.yaml').read_text())
            self.assertNotIn('fixture-value-never-published', (cloud / 'graph/settings.yaml').read_text())

    def test_provider_conversion_and_embedding_equivalence(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'fixture'
            self.workspace(root, ready=False)
            settings = yaml.safe_load((root / 'settings.yaml').read_text())
            for provider, options, model, transport, key in (
                ('openai', {}, 'gpt-4.1-mini', 'litellm', '${OPENAI_API_KEY}'),
                ('deepseek', {}, 'deepseek-flash', 'ir_json_chat', '${DEEPSEEK_API_KEY}'),
                ('zai', {'model': 'glm-5.3-flash'}, 'glm-5.3-flash', 'ir_json_chat', '${ZAI_API_KEY}'),
                ('openclaw', {'api_base': 'https://gateway.example.test/v1'}, 'openclaw/llm-gpt55', 'litellm', '${GRAPHRAG_API_KEY}'),
            ):
                with self.subTest(provider=provider):
                    converted = packaging.cloud_settings(settings, Path('/home/user/app/workspaces/fixture'), provider=provider, **options)
                    chat = converted['completion_models']['default_completion_model']
                    embedding = converted['embedding_models']['default_embedding_model']
                    self.assertEqual((chat['model'], chat['type'], chat['api_key']), (model, transport, key))
                    self.assertEqual(embedding['call_args'].get('extra_headers'),
                                     {'x-openclaw-model': 'openai/text-embedding-3-large'} if provider == 'openclaw' else None)
                    if provider == 'zai':
                        self.assertEqual(chat['api_base'], 'https://api.z.ai/api/paas/v4')
                        self.assertEqual((embedding['model'], embedding['api_base'], embedding['api_key']),
                                         ('text-embedding-3-large', 'https://api.openai.com/v1', '${OPENAI_API_KEY}'))
            for endpoint in (None, 'http://127.0.0.1:18789/v1', 'https://127.0.0.1/v1', 'https://name:password@example.test/v1'):
                with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                    packaging.cloud_settings(settings, Path('/target'), provider='openclaw', api_base=endpoint)
            settings['embedding_models']['default_embedding_model']['call_args']['extra_headers']['x-openclaw-model'] = 'different-model'
            with self.assertRaisesRegex(ValueError, 'not equivalent'):
                packaging.cloud_settings(settings, Path('/target'))

    def test_vector_store_credentials_excluded_and_remote_store_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'fixture'
            self.workspace(root, ready=False)
            settings = yaml.safe_load((root / 'settings.yaml').read_text())
            settings['vector_store'].update(
                api_key='fixture-vector-secret', connection_string='fixture-vector-secret',
                url='https://fixture-vector-secret@example.test', custom_token='fixture-vector-secret',
                index_schema={'entity_description': {'index_name': 'custom_entity_table'}})
            converted = packaging.cloud_settings(settings, Path('/target'))
            self.assertNotIn('fixture-vector-secret', json.dumps(converted))
            self.assertEqual(converted['vector_store']['type'], 'lancedb')
            self.assertEqual(converted['vector_store']['db_uri'], '/target/output/lancedb')
            self.assertEqual(converted['vector_store']['index_schema'], settings['vector_store']['index_schema'])
            settings['vector_store']['type'] = 'azure_ai_search'
            with self.assertRaisesRegex(ValueError, 'local LanceDB'):
                packaging.cloud_settings(settings, Path('/target'))

    def test_stale_index_rejected_and_backup_retains_original_fingerprint(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            original = base / 'source/workspaces/graph'
            self.workspace(original)
            backup = base / 'backup'
            shutil.copytree(original, backup)
            (original / 'input/documents.jsonl').write_text('{"id":"changed"}\n')
            with self.assertRaisesRegex(ValueError, 'stale index'):
                packaging.stage_workspace(original, base / 'stale')
            with patch.object(packaging, 'SPACE_WORKSPACES', base / 'cloud/workspaces'):
                result = packaging.stage_workspace(backup, base / 'staged/snapshot', original_root=original)
            self.assertTrue(result['graph_ready'])
            stamp = json.loads((base / 'staged/snapshot/index-ready.json').read_text())
            self.assertEqual(stamp['source_fingerprint'], json.loads((backup / 'index-ready.json').read_text())['fingerprint'])
            self.assertEqual(stamp['deployment']['target_root'], str(base / 'cloud/workspaces/snapshot'))

    def test_offline_credentials_restore_process_environment(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fixture-original'}, clear=True):
            with packaging.offline_credentials():
                self.assertEqual(os.environ['DEEPSEEK_API_KEY'], 'offline-packaging-placeholder')
            self.assertEqual(os.environ, {'OPENAI_API_KEY': 'fixture-original'})

    def test_linux_fingerprint_does_not_keep_packaging_host_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary).resolve()
            source, staged = base / 'source/graph', base / 'staged/graph'
            self.workspace(source)
            host_home = base / 'host-home'
            host_home.mkdir()
            linux_home = base / 'home'
            linux_home.symlink_to(host_home, target_is_directory=True)
            cloud = linux_home / 'user/app/workspaces'
            with patch.object(packaging, 'SPACE_WORKSPACES', cloud):
                packaging.stage_workspace(source, staged)
            # The Linux container has a real /home directory at the intended path.
            linux_home.unlink()
            shutil.copytree(staged, cloud / 'graph')
            with packaging.offline_credentials():
                config = load_settings(cloud / 'graph')
            stamp = json.loads((cloud / 'graph/index-ready.json').read_text())
            self.assertEqual(stamp['fingerprint'], fingerprint(cloud / 'graph', config))
            self.assertNotIn(str(host_home), json.dumps(config.model_dump(mode='json')))


if __name__ == '__main__':
    unittest.main()
