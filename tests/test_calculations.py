import unittest
from decimal import Decimal

from ir_graphrag.calculations import calculate_change


def comparison(current='10,876', previous='10,351', unit='USD millions'):
    quote = f'Revenue for Q2 2025 and Q2 2024 was {current} and {previous}, respectively.'
    base = dict(source_id='7', quote=quote, unit=unit, basis='GAAP', metric='Revenue')
    return (dict(base, value=current, period='Q2 2025'),
            dict(base, value=previous, period='Q2 2024'),
            [dict(source_id='7', excerpt=quote)])


class FinancialCalculationTests(unittest.TestCase):
    def test_acme_and_other_reported_revenue_changes(self):
        result = calculate_change(*comparison())
        self.assertEqual(result['difference'], '525000000')
        self.assertEqual(result['current']['normalized_value'], '10876000000')
        self.assertAlmostEqual(Decimal(result['growth_percent']), Decimal('5.07197'), places=5)
        self.assertEqual(result['current']['source_id'], '7')
        result = calculate_change(*comparison('193,406', '99,512', 'USD thousands'))
        self.assertEqual(result['difference'], '93894000')
        self.assertAlmostEqual(Decimal(result['growth_percent']), Decimal('94.35445'), places=5)

    def test_scale_conversion_and_whitespace_in_quotes(self):
        current, previous, evidence = comparison('1.5', '1,250')
        current['unit'] = 'USD billions'
        evidence[0]['excerpt'] = evidence[0]['excerpt'].replace(' ', '\n  ')
        result = calculate_change(current, previous, evidence)
        self.assertEqual(result['difference'], '250000000')
        self.assertEqual(result['growth_percent'], '20')
        self.assertEqual(result['unit'], 'USD')

    def test_accounting_negatives_unicode_minus_and_zero_base(self):
        for previous in ('(2,480)', '−2,480', '$0'):
            with self.subTest(previous=previous):
                result = calculate_change(*comparison('$1,000', previous, 'USD'))
                self.assertIsNone(result['growth_percent'])
                self.assertEqual(result['growth_reason'], 'nonpositive_previous')
                self.assertEqual(result['difference'], '1000' if previous == '$0' else '3480')
        result = calculate_change(*comparison('($2.50)', '$1.25', 'USD per share'))
        self.assertEqual(result['difference'], '-3.75')
        self.assertEqual(result['growth_percent'], '-300')

    def test_percentage_points_are_distinct_from_relative_growth(self):
        result = calculate_change(*comparison('25.5', '20', 'percent'))
        self.assertEqual(result['difference'], '5.5')
        self.assertEqual(result['difference_unit'], 'percentage points')
        self.assertEqual(result['growth_percent'], '27.5')
        self.assertEqual(result['difference_basis_points'], '550')
        result = calculate_change(*comparison('10.1', '13.8', 'percent'))
        self.assertEqual(result['difference'], '-3.7')
        self.assertEqual(result['difference_basis_points'], '-370')

    def test_cropped_quotes_cannot_change_original_numeric_tokens(self):
        excerpt = 'Revenue 10,876 99,512. Net loss (14,006), EPS (0.22). Cash $25; debt − 30.'
        evidence = [dict(source_id='7', excerpt=excerpt)]
        current, previous, _ = comparison()
        previous.update(quote='99,512', value='99,512')
        for quote, value in [('876', '876'), ('14,006', '14,006'),
                             ('0.22', '0.22'), ('25', '25'), ('30', '30')]:
            with self.subTest(quote=quote):
                current.update(quote=quote, value=value)
                with self.assertRaises(ValueError):
                    calculate_change(current, previous, evidence)
        current.update(quote='(14,006)', value='(14,006)')
        result = calculate_change(current, previous, evidence)
        self.assertEqual(result['current']['normalized_value'], '-14006000000')
        current.update(quote='10,876', value='10,876')
        result = calculate_change(current, previous, evidence)
        self.assertEqual(result['current']['normalized_value'], '10876000000')

    def test_quotes_source_ids_and_complete_signed_tokens_are_required(self):
        for changes in ({'source_id': '99'}, {'quote': 'An invented excerpt with 10,876'},
                        {'value': '876'}, {'value': '10'}, {'value': '10,876.00'}):
            with self.subTest(changes=changes):
                current, previous, evidence = comparison()
                current.update(changes)
                with self.assertRaises(ValueError):
                    calculate_change(current, previous, evidence)
        for quoted in ('110', '10.5', '(10)', '-10', '−10', '€10', '€ 10', '1,00'):
            with self.subTest(quoted=quoted):
                current, previous, evidence = comparison(quoted, '20', 'USD')
                current['value'] = '10'
                with self.assertRaises(ValueError):
                    calculate_change(current, previous, evidence)

    def test_malformed_amounts_labels_and_incompatible_units_fail(self):
        for value in ('NaN', 'Infinity', '-Infinity', '1e9', '1,23', '1 2', '(10', '10)', '(-10)', ''):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    calculate_change(*comparison(value))
        for changes in ({'unit': 'EUR'}, {'unit': 'percent'}, {'unit': 'USD per share'},
                        {'metric': 'Net income'}, {'basis': 'non-GAAP'},
                        {'period': 'Q2 2024'}, {'period': ''}, {'basis': ''}, {'value': 10876}):
            with self.subTest(changes=changes):
                current, previous, evidence = comparison()
                current.update(changes)
                with self.assertRaises(ValueError):
                    calculate_change(current, previous, evidence)
        with self.assertRaises(ValueError):
            calculate_change(*comparison('$10', '$20', 'percent'))


if __name__ == '__main__':
    unittest.main()
