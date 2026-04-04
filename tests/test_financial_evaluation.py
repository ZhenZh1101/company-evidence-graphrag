import contextlib
import io
import json
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.evaluate_financial import amounts, evaluate, load_cases, main


class EvaluationAmountTests(unittest.TestCase):
    def test_signed_complete_tokens(self):
        values = amounts('−0.22 (14,006) $193,406 11934060 193406.5 $(2,480)')
        self.assertTrue({Decimal('-0.22'), Decimal('-14006'), Decimal('193406'), Decimal('-2480')} <= values)
        self.assertTrue({Decimal('0.22'), Decimal('14006'), Decimal('2480')}.isdisjoint(values))
        self.assertNotIn(Decimal('193406'), amounts('11934060 193406.5 -193,406'))

    def test_wrong_sign_and_bad_citation_fail_saved_answer_checks(self):
        case = ('loss', 'EPS?', {}, ['-0.22'], ['per_share'])
        result = dict(answer='-0.22 美元/股 [Data: Sources (7)]',
                      query_scope={}, context={'sources': [{'id': '7'}]},
                      citation_audit={'status': 'verified_ids', 'invalid_references': []},
                      evidence=[dict(source_id='7', evidence_kind='direct_text',
                                     source_path=str(Path(__file__)), excerpt='Loss per share $(0.22).')])
        self.assertEqual(evaluate(result, case), [])
        result['answer'] = '0.22 美元/股 [Data: Sources (7)]'
        self.assertIn('answer missing exact signed amount -0.22', evaluate(result, case))
        result['answer'] = '-0.22 美元/股 [Data: Sources (99)]'
        self.assertTrue(any('invalid answer citation' in error for error in evaluate(result, case)))


class ExternalCaseTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name).resolve()
        self.path = self.root / 'cases.json'
        self.case = dict(name='custom-revenue', question='What was revenue?',
                         filters={'tickers': ['EXAMPLE'], 'as_of': '2031-05-17', 'fiscal_year': 2031, 'fiscal_quarter': 1},
                         expected=['12.5'], units=['million'])

    def write_cases(self, cases):
        self.path.write_text(json.dumps(cases), encoding='utf-8')

    def result(self):
        return dict(answer='USD 12.5 million [Data: Sources (7)]',
                    query_scope=self.case['filters'].copy(), context={'sources': [{'id': '7'}]},
                    citation_audit={'status': 'verified_ids', 'invalid_references': []},
                    evidence=[dict(source_id='7', evidence_kind='direct_text',
                                   source_path=str(Path(__file__)), excerpt='Revenue was USD 12.5 million.')])

    def test_external_scope_and_evidence_are_checked(self):
        self.write_cases([self.case])
        case, = load_cases(self.path)
        result = self.result()
        self.assertEqual(evaluate(result, case), [])
        result['query_scope']['as_of'] = '2031-05-18'
        self.assertIn('query scope mismatch: as_of', evaluate(result, case))
        result['query_scope'] = self.case['filters'].copy()
        result['evidence'][0]['excerpt'] = 'Revenue was USD 125 million.'
        self.assertIn('cited direct evidence missing exact signed amount 12.5', evaluate(result, case))

    def test_cutoff_is_optional_and_insufficiency_is_generic(self):
        self.case.update(filters={'tickers': ['EXAMPLE']}, expected=[], units=[])
        self.write_cases([self.case])
        case, = load_cases(self.path)
        self.assertEqual(evaluate(dict(answer='No evidence in the current scope.',
                                       query_scope=self.case['filters'], evidence=[]), case), [])

    def test_invalid_external_cases_are_rejected(self):
        invalid = [[], {}, [self.case, self.case],
                   [self.case, {**self.case, 'name': self.case['name'].upper()}],
                   [{**self.case, 'name': '../escape'}], [{**self.case, 'name': '/absolute'}],
                   [{**self.case, 'name': 'nested/file'}], [{**self.case, 'question': ' '}],
                   [{**self.case, 'unexpected': True}], [{**self.case, 'expected': ['NaN']}],
                   [{**self.case, 'expected': ['Infinity']}], [{**self.case, 'expected': [12.5]}],
                   [{**self.case, 'units': ['unknown']}], [{**self.case, 'expected': []}],
                   [{**self.case, 'filters': {'method': 'local'}}],
                   [{**self.case, 'filters': {'tickers': 'EXAMPLE'}}],
                   [{**self.case, 'filters': {'as_of': '2031-02-30'}}],
                   [{**self.case, 'filters': {'fiscal_quarter': True}}]]
        for cases in invalid:
            with self.subTest(cases=cases):
                self.write_cases(cases)
                with self.assertRaises(ValueError):
                    load_cases(self.path)

    def test_offline_and_live_cli_use_external_cases(self):
        self.write_cases([self.case])
        answer_path = self.root / 'answers' / 'custom-revenue.json'
        answer_path.parent.mkdir()
        answer_path.write_text(json.dumps(self.result()), encoding='utf-8')
        argv = ['evaluate_financial', '--root', str(self.root), '--cases', str(self.path)]
        with patch('sys.argv', argv), contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(), 0)
        self.assertEqual(json.loads(output.getvalue())['mode'], 'offline')
        ask = Mock(return_value=self.result())
        with patch('sys.argv', argv + ['--live']), patch.dict('sys.modules', {'ir_graphrag.engine': SimpleNamespace(ask=ask)}), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(), 0)
        ask.assert_called_once_with(self.root, self.case['question'], method='financial', **self.case['filters'])
        self.assertEqual(json.loads(answer_path.read_text(encoding='utf-8')), self.result())

    def test_cli_rejects_empty_cases_before_running(self):
        self.write_cases([])
        argv = ['evaluate_financial', '--root', str(self.root), '--cases', str(self.path)]
        with patch('sys.argv', argv), contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn('nonempty JSON array', error.getvalue())


if __name__ == '__main__':
    unittest.main()
