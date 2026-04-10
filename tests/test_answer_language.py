import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd

from ir_graphrag import cli, engine, financial


class AnswerLanguageTests(unittest.TestCase):
    def test_graph_methods_preserve_query_evidence_and_index(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            engine.initialize(root)
            (root / 'input/documents.jsonl').write_text('{}\n')
            (root / 'manifest.json').write_text(json.dumps({'doc': {'title': 'Original report'}}))
            (root / 'ingestion-report.json').write_text('{"scope": {}}')
            with patch.dict('os.environ', {'GRAPHRAG_API_KEY': 'test-key'}):
                config = engine.load_settings(root)
            fingerprint = engine.fingerprint(root, config)
            (root / 'index-ready.json').write_text(json.dumps({'fingerprint': fingerprint}))
            units = pd.DataFrame([dict(id='unit', human_readable_id=1, document_id='doc', text='Revenue: USD 100 million.')])
            context = {'sources': [{'id': '1', 'text': 'Revenue: USD 100 million.'}]}
            for method in ('basic', 'local', 'global', 'drift'):
                for language, expected in ((None, 'English'), ('zh', 'Simplified Chinese')):
                    with self.subTest(method=method, language=language), \
                         patch.object(engine, 'load_settings', return_value=config), \
                         patch('pandas.read_parquet', side_effect=lambda path: units if path.stem == 'text_units' else pd.DataFrame()), \
                         patch(f'graphrag.api.{method}_search', new_callable=AsyncMock,
                               return_value=('Result [Data: Sources (1)]', context)) as search:
                        options = {} if language is None else {'language': language}
                        result = engine.ask(root, '营收是多少？', method=method, **options)
                        args = search.call_args.kwargs
                        self.assertEqual(args['query'], '营收是多少？')
                        self.assertIn(f'Write the answer in {expected}', args['response_type'])
                        self.assertIn('overrides generic instructions', args['response_type'])
                        self.assertEqual(result['language'], language or 'en')
                        self.assertEqual(result['question'], '营收是多少？')
                        self.assertEqual(result['evidence'][0]['excerpt'], units.iloc[0]['text'])
                        self.assertTrue(result['evidence'][0]['cited'])
                        self.assertEqual(engine.fingerprint(root, config), fingerprint)
            # GlobalSearch can return a canned English response without invoking the model.
            from graphrag.prompts.query.global_search_reduce_system_prompt import NO_DATA_ANSWER
            with patch.object(engine, 'load_settings', return_value=config), \
                 patch('pandas.read_parquet', return_value=pd.DataFrame()), \
                 patch('graphrag.api.global_search', new_callable=AsyncMock, return_value=(NO_DATA_ANSWER, {})):
                result = engine.ask(root, 'What is revenue?', method='global', language='zh')
                self.assertIn('数据不足', result['answer'])
                self.assertEqual(result['language'], 'zh')

    def test_financial_method_receives_language_separately_from_filters(self):
        with tempfile.TemporaryDirectory() as temp, \
             patch.object(financial, 'answer', return_value={'language': 'zh'}) as answer:
            root = Path(temp)
            engine.ask(root, 'Revenue?', method='financial', language='zh', tickers=['ACME'])
            self.assertEqual(answer.call_args.args, (root.resolve(), 'Revenue?'))
            self.assertEqual(answer.call_args.kwargs['language'], 'zh')
            self.assertEqual(answer.call_args.kwargs['tickers'], ['ACME'])

    def test_financial_no_evidence_defaults_to_english_and_localizes_chinese(self):
        for language in (None, 'en', 'zh', 'unsupported'):
            with self.subTest(language=language), \
                 patch.object(financial, 'search', return_value={'question': '营收？', 'evidence': []}) as search, \
                 patch.object(engine, 'load_settings') as settings:
                options = {} if language is None else {'language': language}
                result = financial.answer(Path('/unused'), '营收？', tickers=['ACME'], **options)
                search.assert_called_once_with(Path('/unused'), '营收？', tickers=['ACME'])
                settings.assert_not_called()
                self.assertEqual(result['language'], 'zh' if language == 'zh' else 'en')
                self.assertIn('没有找到' if language == 'zh' else 'No matching source evidence', result['answer'])
                self.assertEqual(result['context'], {'sources': []})
                self.assertEqual(result['citation_audit']['status'], 'no_citations')

    def test_financial_generation_uses_selected_language_without_translating_sources(self):
        config = SimpleNamespace(completion_models={'default': object()},
                                 basic_search=SimpleNamespace(completion_model_id='default'))

        async def stream(question):
            yield 'Result [Data: Sources (12)]'

        for language, expected in ((None, 'English'), ('zh', 'Simplified Chinese')):
            evidence = dict(source_id='12', excerpt='Revenue: USD 100 million.', title='Original report')
            with self.subTest(language=language), \
                 patch.object(financial, 'search', return_value={'question': '营收？', 'evidence': [evidence]}), \
                 patch.object(engine, 'load_settings', return_value=config), \
                 patch('graphrag_llm.completion.completion_factory.create_completion'), \
                 patch('graphrag.query.structured_search.basic_search.search.BasicSearch') as search:
                search.return_value.stream_search.side_effect = stream
                options = {} if language is None else {'language': language}
                result = financial.answer(Path('/unused'), '营收？', **options)
                args = search.call_args.kwargs
                self.assertIn(f'Write the answer in {expected}', args['system_prompt'])
                self.assertIn(f'Write the answer in {expected}', args['response_type'])
                self.assertNotIn("Answer in the user's language.", args['system_prompt'])
                search.return_value.stream_search.assert_called_once_with('营收？')
                context = args['context_builder'].build_context('营收？')
                self.assertIn(evidence['excerpt'], context.context_chunks)
                self.assertEqual(result['evidence'][0]['excerpt'], evidence['excerpt'])
                self.assertTrue(result['evidence'][0]['cited'])
                self.assertEqual(result['language'], language or 'en')

    def test_cli_language_default_and_saved_output(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            for language in (None, 'zh'):
                output = root / 'answer.json'
                args = ['ir-graphrag', 'ask', '--root', temp, '营收？', '--output', str(output)]
                if language:
                    args += ['--language', language]
                result = {'answer': 'Result', 'evidence': [], 'language': language or 'en'}
                with self.subTest(language=language), patch('sys.argv', args), \
                     patch('sys.stdout', new_callable=io.StringIO) as stdout, \
                     patch.object(engine, 'ask', return_value=result) as ask:
                    self.assertEqual(cli.main(), 0)
                    self.assertEqual(ask.call_args.kwargs['language'], language or 'en')
                    self.assertEqual(json.loads(output.read_text())['language'], language or 'en')
                    self.assertEqual(json.loads(stdout.getvalue())['language'], language or 'en')


if __name__ == '__main__':
    unittest.main()
