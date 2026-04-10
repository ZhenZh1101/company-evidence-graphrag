import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest



class UIResearchTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        workspaces = Path(temporary.name) / 'workspaces'
        self.root = workspaces / 'financial-research'
        self.graph_root = workspaces / 'graph-library'
        for root, records, segments in [(self.root, 3, 15), (self.graph_root, 6, 24)]:
            root.mkdir(parents=True)
            (root / 'settings.yaml').write_text('{}\n')
            (root / 'ingestion-report.json').write_text(json.dumps({
                'records_selected': records, 'segments': segments,
                'companies': {'ACME': 2, 'OTHER': 1}, 'issues': [],
            }))
        (self.graph_root / 'index-ready.json').write_text(json.dumps({
            'counts': {'entities': 7, 'relationships': 11},
        }))
        project = Path(__file__).resolve().parents[1]
        source = (project / 'app.py').read_text()
        source = source.replace('PROJECT = Path(__file__).resolve().parent',
                                f'PROJECT = Path({str(project)!r})')
        source = source.replace("WORKSPACES = PROJECT / 'workspaces'",
                                f'WORKSPACES = Path({str(workspaces)!r})')
        self.app = AppTest.from_string(source, default_timeout=10)
        self.app.run()
        self.app.selectbox('workspace').select(self.root).run()
        self.assertFalse(self.app.exception)

    def set_filters(self, root):
        self.app.multiselect(f'tickers:{root}').set_value(['ACME', 'OTHER'])
        self.app.text_input(f'as_of:{root}').set_value('2026-09-30')
        self.app.number_input(f'fiscal_year:{root}').set_value(2026)
        self.app.selectbox(f'fiscal_quarter:{root}').select('2')
        self.app.run()

    def ask(self, root):
        question = 'Compare revenue and explain the sources.'
        self.app.text_area(f'question:{root}').set_value(question).run()

        def answer(command, **kwargs):
            output = Path(command[command.index('--output') + 1])
            output.parent.mkdir(exist_ok=True)
            output.write_text(json.dumps({
                'question': question, 'method': command[command.index('--method') + 1],
                'answer': 'Fixture answer.', 'evidence': [],
            }))
            return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

        with patch('subprocess.run', side_effect=answer) as run:
            self.app.button('ask').click().run()
        self.assertFalse(self.app.exception)
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command[3:7], ['ask', '--root', str(root), question])
        return command

    def test_financial_filters_reach_the_search_command(self):
        self.app.run()
        self.set_filters(self.root)
        command = self.ask(self.root)
        self.assertEqual(command[command.index('--method') + 1], 'financial')
        tickers = [command[index + 1] for index, value in enumerate(command) if value == '--ticker']
        self.assertEqual(tickers, ['ACME', 'OTHER'])
        for flag, value in [('--as-of', '2026-09-30'), ('--fiscal-year', '2026'),
                            ('--fiscal-quarter', '2')]:
            self.assertEqual(command[command.index(flag) + 1], value)

    def test_graph_search_does_not_reuse_financial_filters(self):
        self.app.run()
        self.app.selectbox('workspace').select(self.graph_root).run()
        self.set_filters(self.graph_root)
        self.app.selectbox(f'method:{self.graph_root}').select('global').run()
        command = self.ask(self.graph_root)
        self.assertEqual(command[command.index('--method') + 1], 'global')
        for flag in ('--ticker', '--as-of', '--fiscal-year', '--fiscal-quarter'):
            self.assertNotIn(flag, command)

    def test_workspace_switch_updates_metrics_and_isolates_saved_answers(self):
        answers = self.root / 'answers'
        answers.mkdir()
        answer = 'This answer belongs only to the financial workspace.'
        (answers / 'saved.json').write_text(json.dumps({
            'question': 'Financial workspace question.', 'method': 'financial',
            'answer': answer, 'evidence': [],
        }))
        self.app.run()
        self.assertFalse(self.app.exception)
        self.assertTrue({'3', '15'}.issubset({metric.value for metric in self.app.metric}))
        self.assertEqual(len(self.app.selectbox(f'method:{self.root}').options), 1)
        self.assertIn(answer, [item.value for item in self.app.markdown])

        self.app.selectbox('workspace').select(self.graph_root).run()
        self.assertFalse(self.app.exception)
        values = {metric.value for metric in self.app.metric}
        self.assertTrue({'6', '24'}.issubset(values))
        self.assertTrue({'3', '15'}.isdisjoint(values))
        self.assertGreater(len(self.app.selectbox(f'method:{self.graph_root}').options), 1)
        self.assertNotIn(answer, [item.value for item in self.app.markdown])

        self.app.selectbox('workspace').select(self.root).run()
        self.assertFalse(self.app.exception)
        self.assertIn(answer, [item.value for item in self.app.markdown])


if __name__ == '__main__':
    unittest.main()
