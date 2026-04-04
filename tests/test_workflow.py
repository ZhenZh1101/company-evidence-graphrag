import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from ir_graphrag.ingest import candidates, extract, html_text, prepare, write_json
from ir_graphrag.engine import fingerprint, initialize, load_settings, resolve_evidence, merge_contexts


class WorkflowTests(unittest.TestCase):
    def test_openclaw_transport_preserves_upstream_graph_format(self):
        from ir_graphrag.openclaw import encode_messages, decode_response, MARKER
        from graphrag_llm.types import LLMCompletionResponse
        from graphrag.index.operations.extract_graph.graph_extractor import GraphExtractor
        messages, graph = encode_messages([{'role': 'user', 'content': MARKER + ' ("entity"<|><entity_name><|>ORGANIZATION<|>description)'}])
        self.assertTrue(graph)
        self.assertNotIn('<', messages[0]['content'])
        raw = '("entity"|||ACME|||ORGANIZATION|||Acme sells aircraft.)##("relationship"|||ACME|||CUSTOMER|||Acme supplies the customer.|||8)##END_OF_GRAPH'
        response = LLMCompletionResponse(id='test', created=0, model='test', object='chat.completion', choices=[{'index': 0, 'finish_reason': 'stop', 'message': {'role': 'assistant', 'content': raw}}])
        restored = decode_response(response, graph).content
        entities, relations = GraphExtractor(None, '', 0)._process_result(restored, 'source', '<|>', '##')
        self.assertEqual(entities.iloc[0]['title'], 'ACME')
        self.assertEqual(relations.iloc[0]['weight'], 8)
        passthrough, graph = encode_messages([{'role': 'user', 'content': 'ordinary question'}])
        self.assertFalse(graph)
        self.assertEqual(passthrough[0]['content'], 'ordinary question')

    def test_archive_scope_dedup_provenance_and_traversal(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            data = base / 'ACME_archive'
            data.mkdir()
            rows = []
            for i, published in enumerate(['2025-01-01', '2025-01-01', None, '2026-01-01']):
                folder = data / str(i)
                folder.mkdir()
                (folder / 'page.txt').write_text('Revenue was USD 100 million for fiscal 2024. ' * 3)
                (folder / 'streaming_links.txt').write_text('Must not become document text. ' * 4)
                row = dict(folder=str(i), title='Annual results', category='earnings', publication_date=published)
                rows.append(row)
                write_json(folder / 'meta.json', dict(ticker='ACME', url='https://example.test/results'))
            write_json(data / 'index.json', rows)
            root = base / 'workspace'
            report = prepare(root, [data], since='2025-01-01', until='2025-12-31')
            self.assertEqual((report['records_selected'], report['segments'], report['duplicates']), (2, 1, 1))
            manifest = json.loads((root / 'manifest.json').read_text())
            doc = next(iter(manifest.values()))
            self.assertEqual(doc['ticker'], 'ACME')
            self.assertEqual(len(doc['aliases']), 1)
            self.assertTrue(Path(doc['source_path']).is_file())
            self.assertEqual(doc['publication_date'], '2025-01-01')
            for i, cutoff in enumerate(('20250101', '2025-W01-3')):
                scoped = prepare(base / f'cutoff-{i}', [data], until=cutoff)
                self.assertEqual(scoped['records_selected'], 2)
                self.assertEqual(scoped['scope']['until'], '2025-01-01')
            with self.assertRaises(ValueError):
                prepare(root, [data])
            write_json(data / 'index.json', [dict(folder='../escape', title='escape', category='x')])
            with self.assertRaises(ValueError):
                prepare(base / 'escape-output', [data])
            self.assertFalse((base / 'escape-output/input/documents.jsonl').exists())

    def test_same_text_different_issuers_and_dates_is_preserved(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            sources = []
            for ticker in ('AAA', 'BBB'):
                data = base / ticker
                data.mkdir()
                (data / 'statement.txt').write_text('Both companies report similar revenue figures; preserve issuer provenance.')
                sources.append(data)
            result = prepare(base / 'output', sources)
            self.assertEqual(result['segments'], 2)
            self.assertEqual(result['companies'], {'AAA': 1, 'BBB': 1})

    def test_financial_table_and_sec_body_selection(self):
        text = html_text('<body><nav>menu</nav><script>bad()</script><ix:hidden>hidden fact</ix:hidden><table><tr><th>Year</th><th>Revenue USD millions</th></tr><tr><td>2025</td><td>1,250</td></tr></table></body>')
        self.assertIn('2025 | 1,250', text)
        self.assertNotIn('hidden fact', text)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            (folder / 'sec_documents').mkdir()
            for name in ('filing.htm', 'exhibit.htm', 'R1.htm', 'a-index.html'):
                (folder / 'sec_documents' / name).write_text('<p>filing</p>')
            (folder / 'page.html').write_text('<p>Wrapper</p>')
            chosen = candidates(folder, dict(category='sec_filings', sec_submission={'primaryDocument': 'filing.htm'}))
            self.assertEqual({p.name for p in chosen}, {'filing.htm', 'exhibit.htm'})

    def test_sparse_files_are_reported_and_no_empty_corpus_is_committed(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            (base / 'input').mkdir()
            (base / 'input/empty.txt').write_text('')
            with self.assertRaises(ValueError):
                prepare(base / 'output', [base / 'input'])
            report = json.loads((base / 'output/ingestion-report.json').read_text())
            self.assertEqual(report['issues'][0]['reason'], 'empty_or_sparse_text_requires_review')
            self.assertFalse((base / 'output/input/documents.jsonl').exists())

    def test_settings_paths_credentials_and_staleness(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            initialize(root)
            (root / 'input/documents.jsonl').write_text('{}\n')
            write_json(root / 'manifest.json', {})
            before = Path.cwd()
            with patch.dict('os.environ', {'GRAPHRAG_API_KEY': 'test-key-with:$symbols'}):
                config = load_settings(root)
            self.assertEqual(Path.cwd(), before)
            self.assertEqual(config.completion_models['default_completion_model'].api_key, 'test-key-with:$symbols')
            self.assertEqual(config.vector_store.vector_size, 1536)
            self.assertEqual(config.input.file_pattern, r'.*\.jsonl$')
            self.assertEqual(Path(config.input_storage.base_dir), (root / 'input').resolve())
            first = fingerprint(root, config)
            (root / 'input/documents.jsonl').write_text('{"changed":true}\n')
            self.assertNotEqual(fingerprint(root, config), first)

    def test_source_ids_resolve_through_graph_to_original_documents(self):
        units = [dict(id='unit', human_readable_id=7, document_id='doc', text='Revenue $100 million')]
        tables = dict(text_units=units, entities=[dict(human_readable_id=3, text_unit_ids=['unit'])],
                      communities=[dict(community=2, text_unit_ids=['unit'])])
        manifest = {'doc': dict(title='Annual report', source_path='/data/report.pdf', locator='page 4')}
        evidence = resolve_evidence({'sources': [{'id': '7'}], 'entities': [{'id': '3'}], 'reports': [{'id': '2'}]}, tables, manifest)
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0]['references'], ['Sources:7', 'Entities:3', 'Reports:2'])
        self.assertEqual(evidence[0]['evidence_kind'], 'direct_text')
        self.assertEqual(evidence[0]['locator'], 'page 4')
        combined = merge_contexts([{'sources': [{'id': '7', 'text': 'first'}]},
                                   {'sources': [{'id': '8', 'text': 'second'}, {'id': '7', 'text': 'first'}]}])
        self.assertEqual([row['id'] for row in combined['sources']], ['7', '8'])


if __name__ == '__main__':
    unittest.main()
