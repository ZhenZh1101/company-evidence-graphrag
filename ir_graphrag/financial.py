"""Financial evidence retrieval, independent of expensive graph construction.

SQLite FTS5 provides exact-term retrieval; GraphRAG BasicSearch generates the answer.
GraphRAG local/global/DRIFT remain available for entity and thematic questions.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date
from pathlib import Path

from .i18n import DEFAULT_LANGUAGE, normalize_language

# Small, explicit query expansion for English filings queried in Chinese.
TERMS = {
    '营收': 'revenue revenues sales', '收入': 'revenue revenues sales', '销售额': 'sales revenue',
    '净利润': 'net income earnings', '净亏损': 'net loss', '每股收益': 'earnings per share EPS',
    '每股亏损': 'loss per share', '营业利润': 'operating income', '营业利润率': 'operating margin rate',
    '毛利': 'gross profit margin', '现金流': 'cash flow', '自由现金流': 'free cash flow',
    '指引': 'guidance outlook', '核心': 'core', '调整后': 'adjusted non-GAAP',
    '订单': 'backlog orders', '债务': 'debt borrowings', '资本开支': 'capital expenditures',
    '回购': 'repurchase buyback', '股息': 'dividend', '第一季度': 'first quarter Q1',
    '第二季度': 'second quarter Q2', '第三季度': 'third quarter Q3', '第四季度': 'fourth quarter Q4',
    '一季度': 'first quarter Q1', '二季度': 'second quarter Q2', '三季度': 'third quarter Q3',
    '四季度': 'fourth quarter Q4', '全年': 'full year annual',
}
STOP = set('what how much is are was were the a an of for and in to its did does please give with compare versus'.split())


def corpus_hash(root: Path) -> str:
    value = hashlib.sha256(b'financial-evidence-v2')
    for name in ('input/documents.jsonl', 'manifest.json'):
        with (root / name).open('rb') as handle:
            value.update(hashlib.file_digest(handle, 'sha256').digest())
    return value.hexdigest()


def passages(text: str):
    """Token-bound passages, with the document/table opening repeated as context."""
    import tiktoken
    tokenizer = tiktoken.get_encoding('cl100k_base')
    tokens = tokenizer.encode(text, disallowed_special=())
    if len(tokens) <= 1400:
        yield text
        return
    # The prefix carries the printed units/column headings of extracted table segments.
    prefix = tokenizer.decode(tokens[:220])
    headers = []
    for match in re.finditer(r'(?im)^.*(?:\bin (?:thousands|millions|billions)\b|\bUSD in\b).{0,100}$', text):
        # Keep the nearest printed table header, not a rounded headline at the top of a release.
        following = '\n'.join(text[match.start():].splitlines()[:8])
        position = len(tokenizer.encode(text[:match.start()], disallowed_special=()))
        headers.append((position, following))
    for start in range(0, len(tokens), 1100):
        body = tokenizer.decode(tokens[start:start + 1400])
        nearby = next((header for position, header in reversed(headers) if position <= start), None)
        context = nearby or prefix
        yield body if start == 0 else '[Earlier printed header; verify applicability]\n' + context + '\n[Passage]\n' + body
        if start + 1400 >= len(tokens):
            break


def ensure_catalog(root: Path) -> Path:
    """Build atomically; caller holds the existing workspace lock."""
    root = root.resolve()
    target = root / 'financial-evidence.sqlite'
    signature = corpus_hash(root)
    if target.exists():
        with sqlite3.connect(target) as db:
            if db.execute('SELECT value FROM state WHERE key = ?', ('corpus_hash',)).fetchone() == (signature,):
                return target
    staging = root / 'financial-evidence.sqlite.tmp'
    staging.unlink(missing_ok=True)
    manifest = json.loads((root / 'manifest.json').read_text())
    try:
        with sqlite3.connect(staging) as db:
            db.executescript('''
                CREATE TABLE state(key TEXT PRIMARY KEY, value TEXT);
                CREATE TABLE documents(id TEXT PRIMARY KEY, ticker TEXT, category TEXT, form TEXT,
                    publication_date TEXT, fiscal_year TEXT, fiscal_quarter TEXT, metadata TEXT);
                CREATE TABLE passages(id INTEGER PRIMARY KEY, document_id TEXT, part INTEGER, text TEXT);
                CREATE VIRTUAL TABLE search USING fts5(title, text, tokenize='unicode61');
            ''')
            with (root / 'input/documents.jsonl').open() as handle:
                for line in handle:
                    row = json.loads(line)
                    meta = manifest[row['id']]
                    db.execute('INSERT INTO documents VALUES (?,?,?,?,?,?,?,?)', (
                        row['id'], meta.get('ticker', '').upper(), meta.get('category'), meta.get('form'),
                        meta.get('publication_date'), str(meta['fiscal_year']) if meta.get('fiscal_year') else None,
                        str(meta['fiscal_quarter']) if meta.get('fiscal_quarter') else None,
                        json.dumps(meta, ensure_ascii=False)))
                    title = ' '.join(str(meta.get(k) or '') for k in ('ticker', 'title', 'fiscal_year', 'fiscal_quarter', 'report_date'))
                    for part, text in enumerate(passages(row['text']), 1):
                        identifier = db.execute('INSERT INTO passages(document_id, part, text) VALUES (?,?,?)',
                                                (row['id'], part, text)).lastrowid
                        db.execute('INSERT INTO search(rowid,title,text) VALUES (?,?,?)', (identifier, title, text))
            db.execute('INSERT INTO state VALUES (?,?)', ('corpus_hash', signature))
        staging.replace(target)
    finally:
        staging.unlink(missing_ok=True)
    return target


def query_terms(question: str) -> str:
    expanded = question
    for phrase, words in TERMS.items():
        if phrase in question:
            expanded += ' ' + words
    tokens = re.findall(r'[A-Za-z][A-Za-z0-9]*|\d{4}|[\u4e00-\u9fff]+', expanded)
    terms = list(dict.fromkeys(t.lower() for t in tokens if t.lower() not in STOP))[:64]
    return ' OR '.join('"' + term + '"' for term in terms)


def search(root: Path, question: str, *, tickers=(), as_of=None, forms=(), fiscal_year=None,
           fiscal_quarter=None, limit=12) -> dict:
    if not question.strip():
        raise ValueError('Question must not be empty')
    if not 1 <= limit <= 30:
        raise ValueError('Evidence limit must be between 1 and 30')
    if as_of:
        as_of = date.fromisoformat(as_of).isoformat()
    if fiscal_quarter is not None and str(fiscal_quarter) not in {'1', '2', '3', '4'}:
        raise ValueError('Fiscal quarter must be 1, 2, 3 or 4')
    if fiscal_year is not None and not re.fullmatch(r'\d{4}', str(fiscal_year)):
        raise ValueError('Fiscal year must be a four-digit year')
    filters, parameters = [], []
    for column, values in [('ticker', [x.upper() for x in tickers]), ('form', list(forms))]:
        if values:
            filters.append(f'd.{column} IN ({",".join("?" for _ in values)})')
            parameters.extend(values)
    if as_of:
        filters.append('d.publication_date IS NOT NULL AND d.publication_date <= ?')
        parameters.append(as_of)
    for field, value in [('fiscal_year', fiscal_year), ('fiscal_quarter', fiscal_quarter)]:
        if value is not None:
            filters.append(f'd.{field} = ?')
            parameters.append(str(value))
    clause = ' AND '.join(filters) or '1=1'
    match = query_terms(question)
    facets = []
    if any(word in match for word in ('"revenue"', '"revenues"', '"sales"')):
        facets.append('"total revenue" OR "total sales"')
        if '"core"' in match:
            facets.extend(['"core revenue"', '"gaap revenue"'])
    if '"loss"' in match or '"income"' in match:
        facets.append('"net loss" OR "net income"')
        if '"core"' in match:
            facets.append('"core net loss" OR "core net income"')
    if '"margin"' in match:
        facets.append('"operating margin"')
    if '"share"' in match or '"eps"' in match:
        facets.append('"loss per share" OR "earnings per share" OR "EPS"')
    path = ensure_catalog(root)
    with sqlite3.connect(path) as db:
        eligible = db.execute(f'SELECT COUNT(*) FROM documents d WHERE {clause}', parameters).fetchone()[0]
        candidates, scores, facet_members = {}, {}, {}
        for query in ([match, *facets] if match else []):
            ranked = db.execute(f'''SELECT p.id, p.document_id, p.part, p.text, d.metadata
                FROM search JOIN passages p ON p.id=search.rowid JOIN documents d ON d.id=p.document_id
                WHERE search MATCH ? AND {clause} ORDER BY bm25(search,2.0,1.0), p.id LIMIT ?''',
                [query, *parameters, limit * 12]).fetchall()
            for rank, row in enumerate(ranked, 1):
                candidates[row[0]] = row
                scores[row[0]] = scores.get(row[0], 0) + 1 / (60 + rank)
            if query in facets:
                facet_members[query] = {row[0] for row in ranked}
        rows = [(*row, -scores[identifier]) for identifier, row in candidates.items()]
    metric_words = []
    for word in ('revenue', 'sales', 'income', 'loss', 'margin', 'EPS', 'cash', 'backlog', 'debt'):
        if re.search(r'\b' + word + r'\b', match, re.I):
            metric_words.append(word)
    def quantitative_row(row):
        text = row[3]
        if not metric_words:
            return False
        # Exact numerical rows should precede generic GAAP definitions for metric questions.
        pattern = r'\b(?:' + '|'.join(metric_words) + r')\b[^\n]{0,180}(?:\d+,\d{3}|\d+\.\d+|\$\s*\d+)'
        return bool(re.search(pattern, text, re.I))
    def exact_table(row):
        return quantitative_row(row) and bool(re.search(r'\bin (?:thousands|millions|billions)\b', row[3], re.I))
    year = re.search(r'\b20\d{2}\b', question)
    quarter = re.search(r'\bq([1-4])\b', question, re.I)
    if not quarter:
        for number, chinese in enumerate('一二三四', 1):
            if f'{chinese}季度' in question:
                quarter = re.search(r'([1-4])', str(number))
                break
    def explicit_period(row):
        if not year or not quarter:
            return False
        meta = json.loads(row[4])
        if str(meta.get('fiscal_year')) == year[0] and str(meta.get('fiscal_quarter')) == quarter[1]:
            return True
        word = ['first', 'second', 'third', 'fourth'][int(quarter[1]) - 1]
        return year[0] in meta.get('title', '') and bool(re.search(rf'\b(?:q{quarter[1]}|{word}[ -]quarter)\b', meta.get('title', ''), re.I))
    rows.sort(key=lambda row: (not explicit_period(row), not exact_table(row), not quantitative_row(row), row[5], row[0]))
    # Reserve one strong result per requested metric so EPS is not crowded out by net-loss tables.
    reserved = [next((row for row in rows if row[0] in identifiers), None) for identifiers in facet_members.values()]
    rows = [row for row in reserved if row is not None] + rows
    evidence, seen = [], set()
    for identifier, document, part, text, metadata, score in rows:
        # Do not spend a context window on the same body in multiple archive formats.
        meta = json.loads(metadata)
        key = (meta.get('ticker'), meta.get('publication_date'), meta.get('report_date'),
               meta.get('fiscal_year'), meta.get('fiscal_quarter'), ' '.join(text.split()))
        if key in seen:
            continue
        seen.add(key)
        evidence.append(dict(**meta, source_id=str(identifier), document_id=document,
                             passage=part, excerpt=text, references=[f'Sources:{identifier}'],
                             evidence_kind='direct_text', retrieval_score=-score))
        if len(evidence) == limit:
            break
    return dict(question=question, evidence=evidence, eligible_documents=eligible,
                corpus_hash=corpus_hash(root), retrieval='FTS5 BM25 + financial metric facets, reciprocal rank fusion, exact-table priority',
                query_scope=dict(tickers=list(tickers), as_of=as_of, forms=list(forms),
                                 fiscal_year=fiscal_year, fiscal_quarter=fiscal_quarter),
                scope_note='Publication cutoff excludes unknown dates. Fiscal filters match explicit metadata only; publication date is never treated as the fiscal period.')


def audit_citations(answer: str, context: dict) -> dict:
    references, invalid = [], []
    for block in re.findall(r'\[Data:\s*([^\]]+)\]', answer):
        for table, identifiers in re.findall(r'([A-Za-z]+)\s*\(([^)]+)\)', block):
            allowed = {str(row.get('id')) for row in context.get(table.lower(), [])}
            for identifier in identifiers.split(','):
                identifier = identifier.strip()
                if identifier == '+more':
                    continue
                reference = f'{table}:{identifier}'
                references.append(reference)
                if identifier not in allowed:
                    invalid.append(reference)
    return dict(references=list(dict.fromkeys(references)), invalid_references=list(dict.fromkeys(invalid)),
                status='invalid' if invalid else ('verified_ids' if references else 'no_citations'),
                note='Checks reference IDs, not whether every claim is entailed by the cited text.')


def answer(root: Path, question: str, *, language=DEFAULT_LANGUAGE, **filters) -> dict:
    from graphrag.query.context_builder.builders import BasicContextBuilder, ContextBuilderResult
    from graphrag.query.structured_search.basic_search.search import BasicSearch
    from graphrag_llm.completion.completion_factory import create_completion
    import pandas as pd
    import tiktoken
    from .engine import RESEARCH_RULES, answer_language_instruction, load_settings

    language = normalize_language(language)
    result = search(root, question, **filters)
    result['language'] = language
    evidence = result['evidence']
    if not evidence:
        message = ('在当前公司、发布日期和财务期间范围内，没有找到匹配的原文证据。无法据此给出财务数值；请检查筛选条件或改写指标名称。'
                   if language == 'zh' else
                   'No matching source evidence was found for the selected company, publication date, and fiscal period. '
                   'Financial figures cannot be provided from this evidence; check the filters or rephrase the metric name.')
        result.update(answer=message,
                      context={'sources': []}, citation_audit=audit_citations('', {}), method='financial')
        return result
    tokenizer = tiktoken.get_encoding('cl100k_base')
    included, chunks, used = [], [], 0
    for source in evidence:
        header = {k: source.get(k) for k in ('source_id', 'ticker', 'title', 'publication_date', 'fiscal_year', 'fiscal_quarter', 'report_date', 'locator')}
        text = json.dumps(header, ensure_ascii=False) + '\n' + source['excerpt']
        size = len(tokenizer.encode(text, disallowed_special=()))
        if used + size > 14000:
            continue
        used += size
        included.append(source)
        chunks.append(text)
    if not included:
        raise ValueError('Retrieved evidence exceeds the context budget')
    context = {'sources': [{'id': s['source_id'], 'text': s['excerpt']} for s in included]}

    class FinancialContext(BasicContextBuilder):
        def build_context(self, query, **kwargs):
            return ContextBuilderResult(context_chunks='\n\n---\n\n'.join(chunks), context_records={'sources': pd.DataFrame(context['sources'])})

    config = load_settings(root, require_embeddings=False)
    model = create_completion(config.completion_models[config.basic_search.completion_model_id])
    prompt = '''You answer financial research questions from the source records below.
{context_data}
Response format: {response_type}
Cite factual statements as [Data: Sources (source_id)]. Only use IDs supplied above.
For numerical questions, show a compact table: metric, fiscal period, amount, unit/currency,
GAAP or non-GAAP basis, actual or guidance, and source. Read the printed column headers:
quarter amounts and year-to-date amounts in adjacent columns are different periods.
Use exact disclosed values when available; do not compute precise growth from rounded headlines.
Accounting parentheses indicate negative values. Percent change and percentage-point change
are different; 1 percentage point is 100 basis points. Do not substitute a forecast or a different
quarter when actual results are missing. Do not combine GAAP and adjusted/core values.
Do not call model arithmetic verified. Source dates below are publication dates, not fiscal dates.
If the question cannot be answered, specify exactly which evidence or period is missing.
''' + RESEARCH_RULES.replace("Answer in the user's language.", answer_language_instruction(language))
    query_engine = BasicSearch(model=model, context_builder=FinancialContext(), system_prompt=prompt,
                              response_type='A concise answer with source citations. ' + answer_language_instruction(language))

    async def generate():
        return ''.join([chunk async for chunk in query_engine.stream_search(question)])
    import asyncio
    text = asyncio.run(generate())
    if not text.strip():
        raise RuntimeError('The model returned an empty answer')
    audit = audit_citations(text, context)
    if audit['invalid_references']:
        raise RuntimeError('The model returned unknown evidence IDs; this answer was not accepted. Retry the question.')
    for source in included:
        source['cited'] = f"Sources:{source['source_id']}" in audit['references']
    result.update(answer=text, evidence=included, context=context, citation_audit=audit, method='financial',
                  evidence_note='Direct source passages selected before generation. Citation validation checks IDs only; verify fiscal columns and metric definitions against the original.')
    return result
