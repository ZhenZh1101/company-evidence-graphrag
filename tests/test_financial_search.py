import json
import tempfile
import unittest
from pathlib import Path

from ir_graphrag.financial import search, audit_citations, passages


class FinancialSearchTests(unittest.TestCase):
    def test_filters_precede_retrieval_and_index_invalidates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'input').mkdir()
            manifest, rows = {}, []
            for i, (ticker, published, year) in enumerate([
                ('ACME', '2026-07-21', 2026), ('OTHER', '2026-06-23', 2026),
                ('ACME', '2026-10-20', 2026), ('ACME', None, 2026), ('ACME', '2025-07-20', 2025)]):
                identifier = str(i)
                manifest[identifier] = dict(ticker=ticker, publication_date=published,
                    fiscal_year=year, fiscal_quarter=2, title='Quarterly financial results', category='quarterly_results', form=None)
                rows.append(dict(id=identifier, text=f'Total sales for the quarter were {10000+i} million dollars.'))
            (root / 'manifest.json').write_text(json.dumps(manifest))
            (root / 'input/documents.jsonl').write_text('\n'.join(json.dumps(r) for r in rows))
            result = search(root, '二季度销售额', tickers=['acme'], as_of='2026-09-26', fiscal_year=2026, fiscal_quarter=2)
            self.assertEqual(result['eligible_documents'], 1)
            self.assertEqual([s['document_id'] for s in result['evidence']], ['0'])
            self.assertEqual(search(root, '销售额', as_of='2020-01-01')['evidence'], [])
            for cutoff in ('20260101', '2026-W01-1'):
                self.assertEqual(search(root, '销售额', as_of=cutoff)['eligible_documents'], 1)
            # Preserve fiscal period separate from publication year and invalidate on metadata edits.
            manifest['0']['fiscal_year'] = 2025
            (root / 'manifest.json').write_text(json.dumps(manifest))
            self.assertEqual(search(root, '销售额', tickers=['ACME'], as_of='2026-09-26', fiscal_year=2026)['evidence'], [])

    def test_sql_query_input_and_reference_ids_are_validated(self):
        from ir_graphrag.financial import query_terms
        self.assertNotIn('DROP TABLE', query_terms('revenue "; DROP TABLE documents; --'))
        context = {'sources': [{'id': '12'}]}
        self.assertEqual(audit_citations('Result [Data: Sources (12)]', context)['status'], 'verified_ids')
        self.assertEqual(audit_citations('Result [Data: Sources (12, 99); Reports (2)]', context)['invalid_references'], ['Sources:99', 'Reports:2'])
        self.assertEqual(audit_citations('No evidence', context)['status'], 'no_citations')

    def test_multiple_metrics_and_explicit_quarter_are_not_crowded_out(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'input').mkdir()
            rows, manifest = [], {}
            for i in range(20):
                text = 'USD in thousands. Net loss (14,006). Core net loss (2,480).'
                title = 'First Quarter 2026 Results'
                if i == 18:
                    text = 'USD per share. Net loss per share (0.22).'
                if i == 19:
                    title = 'Second Quarter 2026 Results'
                    text = 'USD per share. Net loss per share (0.77).'
                rows.append(dict(id=str(i), text=text))
                manifest[str(i)] = dict(ticker='OTHER', title=title, publication_date='2026-06-23', category='ir_news')
            (root / 'manifest.json').write_text(json.dumps(manifest))
            (root / 'input/documents.jsonl').write_text('\n'.join(json.dumps(row) for row in rows))
            evidence = search(root, 'Example Systems 2026 Q1 净亏损、每股亏损及 Core 净亏损', tickers=['OTHER'], limit=3)['evidence']
            self.assertIn('18', [source['document_id'] for source in evidence])
            self.assertNotIn('19', [source['document_id'] for source in evidence[:2]])

    def test_equal_text_preserves_different_issuers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'input').mkdir()
            manifest = {ticker: dict(ticker=ticker, title='Quarterly results', publication_date='2026-07-01')
                        for ticker in ('AAA', 'BBB')}
            (root / 'manifest.json').write_text(json.dumps(manifest))
            (root / 'input/documents.jsonl').write_text('\n'.join(json.dumps(dict(id=t, text='Revenue $100 million.')) for t in manifest))
            evidence = search(root, '营收')['evidence']
            self.assertEqual({row['ticker'] for row in evidence}, {'AAA', 'BBB'})

    def test_long_passage_keeps_table_opening(self):
        text = 'Three Months Ended June 30, 2026 and 2025. USD in millions.\n' + '\n'.join(f'Financial item {i} | 1,200 | (300)' for i in range(500))
        chunks = list(passages(text))
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all('USD in millions' in chunk for chunk in chunks))
        self.assertTrue(any('Financial item 499' in chunk for chunk in chunks))
        release = 'Rounded headline: $191.3 million\n' + ('Unrelated discussion. ' * 1400)
        release += '\n(in thousands)\nThree Months Ended March 31\n2026 2025\n' + ('Other adjustment | 1,200 | 900\n' * 200)
        release += 'Core net loss | (2,480) | (14,713)'
        tail = list(passages(release))[-1]
        self.assertIn('(in thousands)', tail)
        self.assertIn('Three Months Ended March 31', tail)
        self.assertNotIn('$191.3 million', tail)


if __name__ == '__main__':
    unittest.main()
