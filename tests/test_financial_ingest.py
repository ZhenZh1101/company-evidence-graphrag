import json
import tempfile
import unittest
from pathlib import Path

from ir_graphrag.ingest import extract, financial_record, html_text, prepare, write_json


class FinancialIngestionTests(unittest.TestCase):
    def test_table_spans_accounting_signs_and_nested_rows(self):
        raw = '''<body><h2>Consolidated statements of income</h2><p>USD in millions</p>
        <table><tr><th rowspan="2">Measure</th><th colspan="2">Three months ended June 30</th>
        <th>Six months ended June 30</th></tr><tr><th>2026</th><th>2025</th><th>2026</th></tr>
        <tr><td rowspan="2">Interest expense</td><td>( 161 )</td><td>(173)</td><td>(323)</td></tr>
        <tr><td>-2.5</td><td>—</td><td>0</td></tr></table>
        <table><tr><td>Net loss</td><td>$</td><td>(</td><td>2,480</td><td>)</td>
        <td>−</td><td>5</td></tr></table>
        <table><tr><td>Nested table wrapper<table><tr><td>Revenue</td><td>123</td></tr></table>
        </td></tr></table></body>'''
        text = html_text(raw)
        self.assertIn('Three months ended June 30 |  | Six months ended June 30', text)
        self.assertIn('Measure | 2026 | 2025 | 2026', text)
        self.assertIn('Interest expense | (161) | (173) | (323)', text)
        self.assertIn('Interest expense | -2.5 | — | 0', text)
        self.assertIn('$(2,480)', text)
        self.assertIn('−5', text)
        self.assertEqual(text.count('Revenue'), 1)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'statement.html'
            path.write_text(raw)
            parts = dict(extract(path))
            self.assertNotIn('Interest expense', parts['body'])
            self.assertIn('USD in millions', parts['table 1'])
            self.assertIn('Consolidated statements of income', parts['table 1'])
            self.assertEqual(sum(v.count('Revenue') for v in parts.values()), 1)

    def test_explicit_financial_periods_survive_without_publication_inference(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            data = base / 'ACME'
            data.mkdir()
            rows = []
            metas = [
                dict(fiscal_year='2025', fiscal_quarter=4, report_date='2025-12-31'),
                dict(fiscal_year='2026', fiscal_quarter=1, report_date='2026-03-31'),
                dict(sec_submission={'reportDate': '2025-12-31'}, report_year='2025'),
                {},
            ]
            for i, meta in enumerate(metas):
                folder = data / str(i)
                folder.mkdir()
                (folder / 'page.txt').write_text('Net earnings and diluted earnings per share are reported in USD. ' * 2)
                rows.append(dict(folder=str(i), title='Annual financial results', category='earnings',
                                 publication_date='2026-04-03'))
                write_json(folder / 'meta.json', meta)
            write_json(data / 'index.json', rows)
            root = base / 'out'
            report = prepare(root, [data])
            self.assertEqual(report['segments'], 4)
            docs = [json.loads(line) for line in (root / 'input/documents.jsonl').read_text().splitlines()]
            manifest = json.loads((root / 'manifest.json').read_text())
            self.assertEqual(docs[0]['publication_date'], '2026-04-03')
            self.assertEqual((docs[0]['fiscal_year'], docs[0]['fiscal_quarter'], docs[0]['report_date']),
                             ('2025', 4, '2025-12-31'))
            self.assertEqual(docs[2]['report_year'], '2025')
            self.assertEqual(docs[2]['report_date'], '2025-12-31')
            self.assertIsNone(docs[3]['fiscal_year'])
            self.assertIsNone(docs[3]['fiscal_quarter'])
            self.assertIsNone(docs[3]['report_date'])
            for doc in docs:
                for key in ('report_date', 'fiscal_year', 'fiscal_quarter', 'report_year'):
                    self.assertEqual(doc[key], manifest[doc['id']][key])

    def test_financial_profile_excludes_ownership_and_technical_records(self):
        cases = [
            (dict(category='sec_filings', form='10-Q/A'), True),
            (dict(category='sec_filings', sec_submission={'form': '10-K'}), True),
            (dict(category='sec_filings', form='S-1'), True),
            (dict(category='sec_filings', form='4', title='Earnings ownership'), False),
            (dict(category='sec_filings', form='SCHEDULE 13G/A'), False),
            (dict(category='quarterly_results'), True),
            (dict(category='annual_report'), True),
            (dict(category='earnings_call'), True),
            (dict(category='investor_presentation'), True),
            (dict(category='ir_news', title='Example Systems Announces Strong First Quarter 2026 Results'), True),
            (dict(category='ir_news', title='Example Systems Fast Inference Cloud Business Nearly Quadruples in Second Quarter 2026'), True),
            (dict(category='ir_news', title='New AI Supercomputer Product'), False),
            (dict(category='document', title='ACME 10-Q 2026'), True),
            (dict(category='events_presentations', title='Second Quarter Earnings Call'), True),
            (dict(category='events_presentations', title='Supernova Product Launch'), False),
            (dict(category='blogs', title='Annual financial results and benchmark earnings'), False),
            (dict(category='research_publications', title='Quarterly results'), False),
        ]
        for meta, expected in cases:
            with self.subTest(meta=meta):
                self.assertEqual(financial_record(meta), expected)
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            data = base / 'ACME'
            data.mkdir()
            rows = []
            for i, form in enumerate(('10-K', '10-Q/A', '4')):
                folder = data / str(i)
                folder.mkdir()
                (folder / 'filing.html').write_text(f'<body>Form {form}: Sales and operating income for this reporting period.</body>')
                rows.append(dict(folder=str(i), title=form, category='sec_filings', form=form))
            write_json(data / 'index.json', rows)
            report = prepare(base / 'filtered', [data], profile='financial', categories=['sec_filings'], forms=['10-Q/A', '4'])
            self.assertEqual(report['records_selected'], 1)
            self.assertEqual(report['scope']['profile'], 'financial')
            report = prepare(base / 'unfiltered', [data])
            self.assertEqual(report['records_selected'], 3)
            with self.assertRaises(ValueError):
                prepare(base / 'bad', [data], profile='unknown')

    def test_truncated_html_uses_complete_text_with_audit_issue(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            data = base / 'OTHER'
            folder = data / 'release'
            folder.mkdir(parents=True)
            (folder / 'article.html').write_text('<article>GAAP net income: 100. Non-GAAP reconciliation: [Truncated]</article>')
            (folder / 'page.txt').write_text('GAAP net income: 100. Non-GAAP reconciliation: Core net loss USD (2,480) thousand.')
            write_json(data / 'index.json', [dict(folder='release', title='First Quarter Results', category='ir_news')])
            root = base / 'out'
            report = prepare(root, [data], profile='financial')
            self.assertEqual(report['segments'], 1)
            self.assertEqual(report['issues'][0]['reason'], 'truncated_html_fallback')
            doc = json.loads((root / 'input/documents.jsonl').read_text())
            self.assertIn('(2,480)', doc['text'])
            self.assertNotIn('[Truncated]', doc['text'])
            source = json.loads((root / 'manifest.json').read_text())[doc['id']]
            self.assertEqual(Path(source['source_path']).name, 'page.txt')


if __name__ == '__main__':
    unittest.main()
