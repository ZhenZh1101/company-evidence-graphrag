"""Check caller-supplied financial cases; offline by default, --live uses the gateway."""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
LIMITATION = 'functional gold-case checks, not semantic financial accuracy assessment'
UNITS = {'million': r'百万美元|(?:USD|\$).*?million|million.*?(?:USD|dollars)',
         'thousand': r'千美元|(?:USD|\$).*?thousand|thousand.*?(?:USD|dollars)',
         'per_share': r'美元\s*[/／]\s*股|美元每股|(?:USD|\$).*?per.share|dollars?.*?per.share'}
NUMBER = r'(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?'
TOKENS = re.compile(rf'(?<![\w.,+\-($])(?:\$?\s*\(\s*\$?\s*{NUMBER}\s*\)|(?:[+-]?\s*\$?|\$\s*[+-]?)\s*{NUMBER})(?!\w|[.,][0-9]|\s*\))')


def load_cases(path: Path) -> list[tuple]:
    """Read objects with name, question, filters, expected decimal strings, and units."""
    rows = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(rows, list) or not rows:
        raise ValueError('cases must be a nonempty JSON array')
    cases, names = [], set()
    for index, row in enumerate(rows, 1):
        prefix = f'case {index}: '
        if not isinstance(row, dict) or set(row) != {'name', 'question', 'filters', 'expected', 'units'}:
            raise ValueError(prefix + 'required fields are name, question, filters, expected, units')
        name, question, filters, expected, units = (row[key] for key in ('name', 'question', 'filters', 'expected', 'units'))
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,99}', name) or name.casefold() in names:
            raise ValueError(prefix + 'name must be a unique filename stem of 1-100 letters, digits, underscores, or hyphens')
        if not isinstance(question, str) or not question.strip():
            raise ValueError(prefix + 'question must be a nonempty string')
        if not isinstance(filters, dict) or set(filters) - {'tickers', 'forms', 'as_of', 'fiscal_year', 'fiscal_quarter'}:
            raise ValueError(prefix + 'filters may contain only tickers, forms, as_of, fiscal_year, fiscal_quarter')
        for key, value in filters.items():
            if key in {'tickers', 'forms'}:
                valid = isinstance(value, list) and all(isinstance(item, str) and item.strip() for item in value)
            elif key == 'as_of':
                try:
                    valid = isinstance(value, str) and date.fromisoformat(value).isoformat() == value
                except ValueError:
                    valid = False
            elif key == 'fiscal_year':
                valid = type(value) in (int, str) and bool(re.fullmatch(r'[1-9][0-9]{3}', str(value)))
            else:
                valid = type(value) in (int, str) and str(value) in {'1', '2', '3', '4'}
            if not valid:
                raise ValueError(prefix + f'invalid filter: {key}')
        if not isinstance(expected, list):
            raise ValueError(prefix + 'expected must be an array of finite decimal strings')
        for value in expected:
            try:
                valid = isinstance(value, str) and bool(value.strip()) and Decimal(value).is_finite()
            except InvalidOperation:
                valid = False
            if not valid:
                raise ValueError(prefix + 'expected must be an array of finite decimal strings')
        if not isinstance(units, list) or any(not isinstance(unit, str) or unit not in UNITS for unit in units):
            raise ValueError(prefix + 'units must be an array containing million, thousand, or per_share')
        if not expected and units:
            raise ValueError(prefix + 'scoped insufficiency cases must have empty expected and units arrays')
        cases.append((name, question, filters, expected, units))
        names.add(name.casefold())
    return cases


def amounts(text: str) -> set[Decimal]:
    """Match whole signed tokens, including accounting negatives; never numeric substrings."""
    result = set()
    for match in TOKENS.finditer(text.replace('−', '-').replace('**', '')):
        token = re.sub(r'[\s,$]', '', match.group())
        result.add(-Decimal(token[1:-1]) if token.startswith('(') else Decimal(token))
    return result


def evaluate(result: dict, case: tuple) -> list[str]:
    name, _, filters, expected, units = case
    failures, answer = [], result.get('answer', '')
    evidence = result.get('evidence', [])
    scope = result.get('query_scope', {})
    for key, value in filters.items():
        if scope.get(key) != value:
            failures.append(f'query scope mismatch: {key}')
    if not expected:
        if evidence or not re.search(r'范围|当前|归档|scope|archive', answer, re.I) or not re.search(r'没有|不足|未找到|insufficient|no .*evidence', answer, re.I):
            failures.append('missing scoped insufficiency, or unexpected evidence for unavailable case')
        return failures
    context_ids = {str(row.get('id')) for row in result.get('context', {}).get('sources', [])}
    cited = set()
    for block in re.findall(r'\[Data:\s*([^\]]+)\]', answer):
        for table, identifiers in re.findall(r'([A-Za-z]+)\s*\(([^)]+)\)', block):
            for identifier in identifiers.split(','):
                identifier = identifier.strip()
                if identifier == '+more':
                    continue
                if table != 'Sources' or identifier not in context_ids:
                    failures.append(f'invalid answer citation: {table}:{identifier}')
                cited.add(identifier)
    audit = result.get('citation_audit', {})
    if not cited or audit.get('status') != 'verified_ids' or audit.get('invalid_references'):
        failures.append('citation audit is absent or invalid')
    for source in evidence:
        if not isinstance(source.get('source_path'), str) or not Path(source['source_path']).is_file():
            failures.append(f"source file missing: {source.get('source_id')}")
    direct = [source for source in evidence if source.get('evidence_kind') == 'direct_text' and str(source.get('source_id')) in cited]
    if cited - {str(source.get('source_id')) for source in direct}:
        failures.append('cited IDs do not resolve to direct evidence')
    source_amounts = set().union(*(amounts(source.get('excerpt', '')) for source in direct))
    for value in expected:
        if Decimal(value) not in amounts(answer):
            failures.append(f'answer missing exact signed amount {value}')
        if Decimal(value) not in source_amounts:
            failures.append(f'cited direct evidence missing exact signed amount {value}')
    for unit in units:
        if not re.search(UNITS[unit], answer, re.I):
            failures.append(f'answer missing unit label: {unit}')
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--cases', type=Path, required=True, help='JSON array of caller-supplied evaluation cases.')
    parser.add_argument('--output', type=Path)
    parser.add_argument('--live', action='store_true', help='Sequentially regenerate and save all supplied answers.')
    args = parser.parse_args()
    try:
        supplied_cases = load_cases(args.cases.expanduser())
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    root, cases = args.root.expanduser().resolve(), []
    for case in supplied_cases:
        name, question, filters, _, _ = case
        path = root / 'answers' / f'{name}.json'
        try:
            if args.live:
                from ir_graphrag.engine import ask
                result = ask(root, question, method='financial', **filters)
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            else:
                result = json.loads(path.read_text(encoding='utf-8'))
            failures = evaluate(result, case)
        except Exception as exc:
            message = str(exc) if type(exc).__module__ == 'builtins' and isinstance(exc, (ValueError, FileNotFoundError, RuntimeError)) else 'inspect workspace logs'
            failures = [f'{type(exc).__name__}: {message}']
        cases.append(dict(case=name, passed=not failures, failures=failures, answer_path=str(path)))
    report = dict(passed=all(case['passed'] for case in cases), mode='live' if args.live else 'offline', limitation=LIMITATION, cases=cases)
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + '\n', encoding='utf-8')
    return 0 if report['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
