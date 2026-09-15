import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

from ir_graphrag.auth import hash_password
from ir_graphrag.auth_ui import get_auth_service


TEST_USERS_JSON = json.dumps({'reader': hash_password('ui-test-password'),
                              'reviewer': hash_password('ui-review-password')})


class UILanguageTests(unittest.TestCase):
    def setUp(self):
        auth_environment = patch.dict(os.environ, {'GRAPHRAG_AUTH_USERS_JSON': TEST_USERS_JSON})
        auth_environment.start()
        self.addCleanup(auth_environment.stop)
        get_auth_service.clear()
        self.addCleanup(get_auth_service.clear)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspaces = Path(temporary.name) / 'workspaces'
        self.root = workspaces / 'fixture'
        self.root.mkdir(parents=True)
        (self.root / 'settings.yaml').write_text('{}\n')
        (self.root / 'ingestion-report.json').write_text(json.dumps({
            'records_selected': 1, 'segments': 1, 'companies': {'ACME': 1},
            'categories': {'quarterly_results': 1}, 'issues': [],
        }))
        project = Path(__file__).resolve().parents[1]
        source = (project / 'app.py').read_text()
        self.assertIn("WORKSPACES = PROJECT / 'workspaces'", source)
        source = source.replace('PROJECT = Path(__file__).resolve().parent',
                                f'PROJECT = Path({str(project)!r})')
        source = source.replace("WORKSPACES = PROJECT / 'workspaces'",
                                f'WORKSPACES = Path({str(workspaces)!r})')
        self.source = source
        self.app = AppTest.from_string(source, default_timeout=10)
        self.login(self.app)

    def login(self, app):
        app.run()
        app.text_input('login_username').input('reader')
        app.text_input('login_password').input('ui-test-password')
        app.button('login_submit').click().run()
        self.assertFalse(app.exception)

    def assert_language(self, language):
        self.assertFalse(self.app.exception)
        self.assertEqual(self.app.selectbox('language').value, language)
        self.assertEqual(self.app.query_params['lang'], language)
        title = 'Public Company GraphRAG' if language == 'en' else '上市公司 GraphRAG'
        self.assertEqual(self.app.title[0].value, title)
        question_label = 'Your question' if language == 'en' else '你的问题'
        self.assertEqual(self.app.text_area(f'question:{self.root}').label, question_label)

    def test_english_is_the_default(self):
        self.app.run()
        self.assert_language('en')

    def test_initial_language_comes_from_url_with_english_fallback(self):
        for requested, expected in [('zh', 'zh'), ('unsupported', 'en')]:
            with self.subTest(requested=requested):
                app = AppTest.from_string(self.source, default_timeout=10)
                app.query_params['lang'] = requested
                self.login(app)
                self.app = app
                self.assert_language(expected)

    def test_switching_languages_preserves_question_filters_and_import(self):
        self.app.run()
        text_inputs = {
            'import_name': 'new-library',
            'import_categories': 'quarterly_results',
            'import_since': '2025-01-01',
            'import_until': '2025-12-31',
            f'as_of:{self.root}': '2026-09-30',
        }
        text_areas = {
            'import_datasets': '/data/ACME\n/data/OTHER',
            f'question:{self.root}': 'Compare quarterly revenue for ACME.',
        }
        numbers = {'import_limit': 7, f'fiscal_year:{self.root}': 2025}
        selections = {'import_profile': 'all', f'fiscal_quarter:{self.root}': '2'}
        for kind, values in [(self.app.text_input, text_inputs),
                             (self.app.text_area, text_areas),
                             (self.app.number_input, numbers),
                             (self.app.selectbox, selections)]:
            for key, value in values.items():
                kind(key).set_value(value)
        self.app.multiselect(f'tickers:{self.root}').set_value(['ACME'])
        self.app.run()

        for language in ['zh', 'en']:
            with self.subTest(language=language):
                self.app.selectbox('language').select(language).run()
                self.assert_language(language)
                for key in ('workspace', 'import_profile', f'method:{self.root}', f'fiscal_quarter:{self.root}'):
                    widget = self.app.selectbox(key)
                    self.assertTrue(widget.proto.set_value)
                    self.assertEqual(widget.proto.raw_value, widget.format_func(widget.value))
                for kind, values in [(self.app.text_input, text_inputs),
                                     (self.app.text_area, text_areas),
                                     (self.app.number_input, numbers),
                                     (self.app.selectbox, selections)]:
                    for key, value in values.items():
                        self.assertEqual(kind(key).value, value, key)
                self.assertEqual(self.app.multiselect(f'tickers:{self.root}').value, ['ACME'])

    def test_query_passes_the_selected_answer_language(self):
        self.app.run()
        question = 'What was ACME revenue?'
        self.app.text_area(f'question:{self.root}').set_value(question).run()

        def answer(command, **kwargs):
            self.assertEqual(command[3], 'ask')
            output = Path(command[command.index('--output') + 1])
            self.assertEqual(output.parent, self.root / 'answers')
            output.parent.mkdir(exist_ok=True)
            language = command[command.index('--language') + 1]
            output.write_text(json.dumps({
                'question': question, 'method': 'financial', 'language': language,
                'answer': f'Answer in {language}', 'evidence': [],
            }))
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

        for language in ['en', 'zh']:
            with self.subTest(language=language):
                self.app.selectbox('language').select(language).run()
                if language == 'zh':
                    self.assertIn('Answer in en', [item.value for item in self.app.markdown])
                with patch('subprocess.run', side_effect=answer) as run:
                    next(button for button in self.app.button
                         if button.proto.type == 'primary').click().run()
                self.assertFalse(self.app.exception)
                run.assert_called_once()
                command = run.call_args.args[0]
                self.assertEqual(command[command.index('--language') + 1], language)
                self.assertIn(f'Answer in {language}', [item.value for item in self.app.markdown])

    def test_switching_languages_preserves_calculator_sources_and_values(self):
        evidence = [{
            'source_id': source_id, 'ticker': 'ACME', 'title': 'Earnings',
            'locator': f'page {source_id}', 'references': [f'Sources:{source_id}'],
            'publication_date': '2026-07-01', 'evidence_kind': 'direct_text',
            'url': 'https://example.test/earnings', 'source_path': '/data/report.txt',
            'excerpt': f'Revenue was {value}. Original disclosure.', 'cited': True,
        } for source_id, value in [('1', '1,200'), ('2', '1,000')]]
        answers = self.root / 'answers'
        answers.mkdir()
        (answers / 'saved.json').write_text(json.dumps({
            'question': 'Compare revenue.', 'method': 'financial',
            'answer': 'Saved source-language answer.', 'evidence': evidence,
        }))
        self.app.run()
        self.app.selectbox(f'calc-source:previous:{self.root}').select(evidence[1]).run()
        values = {
            f'calc-metric:{self.root}': 'Revenue',
            f'calc-basis:{self.root}': 'Non-GAAP',
            f'calc-value:current:{self.root}': '1,200',
            f'calc-value:previous:{self.root}': '1,000',
            f'calc-period:current:{self.root}': 'Q2 2026',
            f'calc-period:previous:{self.root}': 'Q2 2025',
        }
        for key, value in values.items():
            self.app.text_input(key).set_value(value)
        for operand, source_id, value in [('current', '1', '1,200'), ('previous', '2', '1,000')]:
            self.app.selectbox(f'calc-unit:{operand}:{self.root}').select('USD thousands')
            self.app.text_area(f'calc-quote:{operand}:{self.root}:{source_id}').set_value(f'Revenue was {value}.')
        self.app.run()

        for language in ['zh', 'en']:
            with self.subTest(language=language):
                self.app.selectbox('language').select(language).run()
                self.assert_language(language)
                self.assertIn('Saved source-language answer.', [item.value for item in self.app.markdown])
                for key, value in values.items():
                    self.assertEqual(self.app.text_input(key).value, value, key)
                for operand, source_id, value in [('current', '1', '1,200'), ('previous', '2', '1,000')]:
                    self.assertEqual(self.app.selectbox(f'calc-source:{operand}:{self.root}').value['source_id'], source_id)
                    self.assertEqual(self.app.selectbox(f'calc-unit:{operand}:{self.root}').value, 'USD thousands')
                    unit = self.app.selectbox(f'calc-unit:{operand}:{self.root}')
                    self.assertTrue(unit.proto.set_value)
                    self.assertEqual(unit.proto.raw_value, '千美元' if language == 'zh' else 'USD thousands')
                    self.assertEqual(self.app.text_area(f'calc-quote:{operand}:{self.root}:{source_id}').value, f'Revenue was {value}.')


if __name__ == '__main__':
    unittest.main()
