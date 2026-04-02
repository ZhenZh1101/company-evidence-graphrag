"""Deterministic changes between two explicitly cited financial figures."""
from __future__ import annotations

import re
from decimal import Decimal, localcontext


_NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?|\.[0-9]+"
_AMOUNT = re.compile(rf"(?P<prefix>[+-]?\$?|\$[+-]?)(?P<number>{_NUMBER})")
_PREFIX = r"(?:(?:[+\-−]\s*)?[$€£¥]?\s*|[$€£¥]\s*[+\-−]\s*)"
_TOKEN = re.compile(
    rf"(?<![\w.,+\-−$€£¥(])(?:\(\s*{_PREFIX}(?:{_NUMBER})\s*\)|{_PREFIX}(?:{_NUMBER}))"
    r"(?!\w|[.,][0-9])"
)
_UNITS = {
    'USD': ('USD', 1),
    'USD thousands': ('USD', 1000),
    'USD millions': ('USD', 1000000),
    'USD billions': ('USD', 1000000000),
    'USD per share': ('USD per share', 1),
    'percent': ('percent', 1),
}


def _amount(value: str) -> tuple[Decimal, str]:
    if re.search(r'[0-9.,]\s+[0-9.,]', value):
        raise ValueError(f'Invalid financial amount: {value!r}')
    text = re.sub(r'\s+', '', value).replace('−', '-')
    accounting = text.startswith('(') and text.endswith(')')
    if accounting:
        text = text[1:-1]
    match = _AMOUNT.fullmatch(text)
    if not match or (accounting and any(c in match['prefix'] for c in '+-')):
        raise ValueError(f'Invalid financial amount: {value!r}')
    number = Decimal(match['number'].replace(',', ''))
    if accounting or '-' in match['prefix']:
        number = number.copy_negate()
    return number, match['number']


def _text(value: str) -> str:
    return ' '.join(value.split())


def _decimal_string(value: Decimal) -> str:
    text = format(value, 'f')
    return (text.rstrip('0').rstrip('.') if '.' in text else text) if value else '0'


def calculate_change(current: dict, previous: dict, evidence: list[dict]) -> dict:
    """Validate quoted numeric tokens, then calculate using Decimal.

    Period, metric and basis labels must agree as specified below, but their
    semantic relationship to the quote is supplied by the caller, not verified.
    Invalid or incompatible operands raise ValueError.
    """
    if not isinstance(evidence, list) or any(not isinstance(row, dict) for row in evidence):
        raise ValueError('Evidence must be a list of source records.')

    def operand(data):
        if not isinstance(data, dict):
            raise ValueError('Each operand must be an object.')
        fields = ('source_id', 'quote', 'value', 'unit', 'period', 'basis', 'metric')
        if any(not isinstance(data.get(k), str) or not data[k].strip() for k in fields):
            raise ValueError('Each operand requires nonempty string source_id, quote, value, unit, period, basis and metric.')
        row = {k: data[k].strip() for k in fields}
        if row['unit'] not in _UNITS:
            raise ValueError(f"Unsupported unit: {row['unit']!r}")
        if '$' in row['value'] and not row['unit'].startswith('USD'):
            raise ValueError('A dollar amount requires a USD unit.')
        quote = _text(row['quote'])
        excerpts = [_text(source['excerpt']) for source in evidence
                    if str(source.get('source_id')) == row['source_id']
                    and isinstance(source.get('excerpt'), str)]
        if not any(quote in excerpt for excerpt in excerpts):
            raise ValueError(f"Quote is absent from evidence source {row['source_id']!r}.")
        parsed = _amount(row['value'])
        for excerpt in excerpts:
            spans = [(match.start(), match.start() + len(quote))
                     for match in re.finditer(rf'(?={re.escape(quote)})', excerpt)]
            for match in _TOKEN.finditer(excerpt):
                literal = match.group().strip()
                start = match.start() + len(match.group()) - len(match.group().lstrip())
                if not any(left <= start and start + len(literal) <= right for left, right in spans):
                    continue
                try:
                    token = _amount(literal)
                except ValueError:
                    continue
                if token == parsed:
                    if '$' in literal and not row['unit'].startswith('USD'):
                        raise ValueError('A dollar amount requires a USD unit.')
                    return row, parsed[0]
        raise ValueError('Quote must cover a complete signed numeric token in the original evidence matching value.')

    current_row, current_value = operand(current)
    previous_row, previous_value = operand(previous)
    for label in ('metric', 'basis'):
        if _text(current_row[label]).casefold() != _text(previous_row[label]).casefold():
            raise ValueError(f'Cannot compare different {label} labels.')
    if _text(current_row['period']).casefold() == _text(previous_row['period']).casefold():
        raise ValueError('Current and previous periods must be distinct.')
    unit, current_scale = _UNITS[current_row['unit']]
    previous_unit, previous_scale = _UNITS[previous_row['unit']]
    if unit != previous_unit:
        raise ValueError('Cannot compare different unit dimensions.')

    with localcontext() as context:
        context.prec = max(28, len(current_row['value']) + len(previous_row['value']) + 12)
        current_normalized = current_value * current_scale
        previous_normalized = previous_value * previous_scale
        difference = current_normalized - previous_normalized
        growth = difference / previous_normalized * 100 if previous_normalized > 0 else None
        current_row['normalized_value'] = _decimal_string(current_normalized)
        previous_row['normalized_value'] = _decimal_string(previous_normalized)
        return dict(
            current=current_row, previous=previous_row, unit=unit,
            difference=_decimal_string(difference),
            difference_unit='percentage points' if unit == 'percent' else unit,
            difference_basis_points=_decimal_string(difference * 100) if unit == 'percent' else None,
            growth_percent=_decimal_string(growth) if growth is not None else None,
            growth_reason='nonpositive_previous' if growth is None else None,
            formula='difference = current - previous; growth_percent = difference / previous * 100',
            note='Numeric tokens are checked against the cited excerpts. Metric, period, unit and basis labels are supplied by the user; their semantic interpretation is not verified.',
        )
