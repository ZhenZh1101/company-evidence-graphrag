"""Read IR archives or ordinary document directories without changing source files."""
from __future__ import annotations

import csv
import hashlib
import json
import re
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path
from xml.etree import ElementTree as ET

from bs4 import BeautifulSoup

SUPPORTED = {'.txt', '.md', '.html', '.htm', '.pdf', '.docx', '.pptx', '.xlsx', '.xls', '.csv', '.xml'}
SKIP_NAMES = {'streaming_links.txt', 'online_viewers.txt', 'unavailable.txt', 'README.txt', 'README.md'}


def digest(value: str) -> str:
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def write_json(path: Path, value) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def inside(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f'Path escapes dataset: {relative}')
    return path


def html_text(raw: str) -> str:
    soup = BeautifulSoup(raw, 'html.parser')
    for tag in soup.find_all(['script', 'style', 'nav', 'footer', 'noscript', 'ix:hidden', 'ix:header']):
        tag.decompose()
    for tag in soup.select('[hidden], [aria-hidden="true"]'):
        tag.decompose()
    for tag in soup.find_all(style=re.compile(r'display\s*:\s*none', re.I)):
        tag.decompose()
    body = soup.find('article') or soup.find('main') or soup.body or soup
    # Keep column positions and numbers together in financial tables.
    for table in list(body.find_all('table')):
        if table.parent is None:
            continue
        lines = [' | '.join(cell.get_text(' ', strip=True) for cell in row.find_all(['th', 'td'], recursive=False))
                 for row in table.find_all('tr')]
        table.replace_with('\n' + '\n'.join(lines) + '\n')
    return body.get_text('\n', strip=True)


def extract(path: Path, issues: list | None = None) -> list[tuple[str, str]]:
    """Return (page/slide/sheet/section locator, text); never silently OCR."""
    suffix = path.suffix.lower()
    if suffix == '.pdf':
        from pypdf import PdfReader
        result = []
        for i, page in enumerate(PdfReader(path).pages, 1):
            try:
                text = page.extract_text(extraction_mode='layout', layout_mode_strip_rotated=False) or ''
            except Exception:
                if issues is not None:
                    issues.append(dict(path=str(path), locator=f'page {i}', reason='pdf_layout_fallback'))
                text = page.extract_text() or ''
            result.append((f'page {i}', text))
        return result
    if suffix in {'.html', '.htm'}:
        return [('body', html_text(path.read_text(encoding='utf-8', errors='replace')))]
    if suffix in {'.docx', '.pptx'}:
        with zipfile.ZipFile(path) as archive:
            names = ['word/document.xml'] if suffix == '.docx' else sorted(
                (n for n in archive.namelist() if re.fullmatch(r'ppt/slides/slide\d+\.xml', n)),
                key=lambda n: int(re.search(r'(\d+)\.xml', n)[1]))
            result = []
            for i, name in enumerate(names, 1):
                root = ET.fromstring(archive.read(name))
                paragraphs = [' '.join(t.text or '' for t in p.iter() if t.tag.endswith('}t'))
                              for p in root.iter() if p.tag.endswith('}p')]
                result.append((f'slide {i}' if suffix == '.pptx' else 'body', '\n'.join(paragraphs)))
            return result
    if suffix == '.xlsx':
        from openpyxl import load_workbook
        book = load_workbook(path, read_only=True, data_only=True)
        try:
            return [(f'sheet {s.title}', '\n'.join(' | '.join('' if c is None else str(c) for c in row)
                                                  for row in s.iter_rows(values_only=True))) for s in book]
        finally:
            book.close()
    if suffix == '.xls':
        import xlrd
        book = xlrd.open_workbook(path)
        try:
            return [(f'sheet {s.name}', '\n'.join(' | '.join(str(c) for c in s.row_values(i))
                                                 for i in range(s.nrows))) for s in book.sheets()]
        finally:
            book.release_resources()
    raw = path.read_text(encoding='utf-8-sig', errors='replace')
    if suffix == '.xml':
        root = ET.fromstring(raw)
        # Ownership filings need field names as well as values.
        def fields(element, prefix=''):
            label = prefix + '/' + element.tag.split('}')[-1]
            if element.text and element.text.strip():
                yield f'{label}: {element.text.strip()}'
            for child in element:
                yield from fields(child, label)
        text = '\n'.join(fields(root))
        return [('body', text)]
    if suffix == '.csv':
        raw = '\n'.join(' | '.join(row) for row in csv.reader(raw.splitlines()))
    if suffix == '.txt' and re.search(r'<(?:html|DOCUMENT|SEC-DOCUMENT)[\s>]', raw, re.I):
        raw = html_text(raw)
    return [('body', raw)]


def candidates(folder: Path, meta: dict) -> list[Path]:
    """Pick one webpage/filing representation, plus distinct attachments/exhibits."""
    files = sorted(p for p in folder.rglob('*') if p.is_file() and p.suffix.lower() in SUPPORTED
                   and p.name not in SKIP_NAMES and not any(x.startswith('.') for x in p.relative_to(folder).parts))
    if meta.get('category', '').startswith('sec_'):
        primary = Path(meta.get('sec_submission', {}).get('primaryDocument', '')).name
        native = [p for p in files if 'sec_documents' in p.parts and (
            p.name == primary or (p.suffix.lower() in {'.html', '.htm'}
                                 and not re.search(r'(?:-index|headers|^R\d+\.)', p.name, re.I)))]
        if native:
            return native
        for name in ('filing.html',):
            if (folder / name).is_file():
                return [folder / name]
        pdfs = [p for p in files if p.suffix.lower() == '.pdf']
        return pdfs[:1]  # IR wrapper pages are not the filing text.
    selected = []
    for name in ('article.html', 'page.txt', 'page.html', 'source_fragment.html'):
        if (folder / name).is_file():
            selected.append(folder / name)
            break
    reserved = {'article.html', 'page.txt', 'page.html', 'source_fragment.html', 'source_listing.html'}
    for path in files:
        if path.name not in reserved:
            selected.append(path)
    return selected


def inventory(dataset: Path, ticker: str | None = None):
    dataset = dataset.expanduser().resolve()
    if not dataset.is_dir():
        raise ValueError(f'Dataset directory not found: {dataset}')
    fallback = ticker or dataset.name.split('_')[0]
    if (dataset / 'index.json').is_file():
        rows = json.loads((dataset / 'index.json').read_text(encoding='utf-8'))
        if not isinstance(rows, list):
            raise ValueError('index.json must contain an array of archive records')
        for row in rows:
            folder = inside(dataset, row['folder'])
            meta_file = folder / 'meta.json'
            meta = json.loads(meta_file.read_text(encoding='utf-8')) if meta_file.is_file() else {}
            meta = {**meta, **row}
            meta['ticker'] = ticker or meta.get('ticker') or fallback
            meta['company'] = meta.get('company') or meta.get('company_name') or meta['ticker']
            yield dataset, folder, meta, candidates(folder, meta)
    else:
        for path in sorted(dataset.rglob('*')):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED or path.name in SKIP_NAMES:
                continue
            if any(x.startswith('.') or x == 'audit' for x in path.relative_to(dataset).parts):
                continue
            yield dataset, path.parent, dict(ticker=fallback, company=fallback, title=path.stem,
                                            category='document', publication_date=None), [path]


def prepare(root: Path, datasets: list[Path], *, ticker=None, categories=(), forms=(),
            since=None, until=None, include_undated=False, limit_records=None, progress=None) -> dict:
    root = root.expanduser().resolve()
    for value in (since, until):
        if value:
            date.fromisoformat(value)
    if since and until and since > until:
        raise ValueError('--since must be no later than --until')
    if limit_records is not None and limit_records < 1:
        raise ValueError('--limit-records must be positive')
    if (root / 'input/documents.jsonl').exists() or (root / 'output').exists():
        raise ValueError('Workspace already contains data. Use a new --root for a new corpus/scope.')
    for dataset in datasets:
        if root.is_relative_to(dataset.expanduser().resolve()):
            raise ValueError('Workspace must be outside the source dataset')
    (root / 'input').mkdir(parents=True, exist_ok=True)
    report = dict(records_seen=0, records_selected=0, files_selected=0, segments=0,
                  duplicates=0, characters=0, companies={}, categories={}, issues=[], scope={
                      'datasets': [str(p.expanduser().resolve()) for p in datasets], 'ticker': ticker,
                      'categories': list(categories), 'forms': list(forms), 'since': since, 'until': until,
                      'include_undated': include_undated, 'limit_records': limit_records})
    manifest, seen, extracted = {}, {}, {}
    companies, category_counts = Counter(), Counter()
    staging = root / 'input/documents.jsonl.tmp'
    try:
        with staging.open('w', encoding='utf-8') as out:
            for dataset in datasets:
                for base, folder, meta, paths in inventory(dataset, ticker):
                    report['records_seen'] += 1
                    if categories and meta['category'] not in categories:
                        continue
                    if forms and meta.get('form') not in forms:
                        continue
                    published = meta.get('publication_date')
                    exact_date = None
                    if published:
                        try:
                            exact_date = date.fromisoformat(published).isoformat()
                        except (ValueError, TypeError):
                            pass
                    if since or until:
                        if not exact_date and not include_undated:
                            continue
                        if exact_date and ((since and exact_date < since) or (until and exact_date > until)):
                            continue
                    if limit_records and report['records_selected'] >= limit_records:
                        continue
                    report['records_selected'] += 1
                    if progress:
                        progress(report['records_selected'], meta['title'])
                    if not paths:
                        report['issues'].append(dict(record=meta['title'], reason='no_supported_body'))
                    urls = {f.get('path', f.get('filename')): f.get('source_url')
                            for f in meta.get('files', []) if isinstance(f, dict)}
                    if meta.get('streaming') or meta.get('category') == 'videos':
                        report['issues'].append(dict(record=meta['title'], reason='linked_media_not_transcribed'))
                    for path in paths:
                        report['files_selected'] += 1
                        try:
                            path = inside(base, str(path.relative_to(base)))
                            with path.open('rb') as handle:
                                byte_hash = hashlib.file_digest(handle, 'sha256').hexdigest()
                            if byte_hash not in extracted:
                                extracted[byte_hash] = extract(path, report['issues'])
                            parts = extracted[byte_hash]
                            for locator, text in parts:
                                text = re.sub(r'\n[ \t]*\n(?:[ \t]*\n)+', '\n\n', text).strip()
                                if len(text) < 30:
                                    report['issues'].append(dict(path=str(path), locator=locator, reason='empty_or_sparse_text_requires_review'))
                                    continue
                                source = dict(ticker=meta['ticker'], company=meta['company'], title=meta['title'],
                                              publication_date=exact_date, publication_period=meta.get('publication_period'),
                                              date_basis=meta.get('date_basis'), category=meta['category'],
                                              form=meta.get('form'), report_date=meta.get('sec_submission', {}).get('reportDate'),
                                              url=urls.get(str(path.relative_to(folder))) or meta.get('url', ''),
                                              source_path=str(path), locator=locator,
                                              content_sha256=digest(text), source_sha256=byte_hash)
                                # Preserve different issuers/dates even when body text is identical.
                                dedup = (meta['ticker'], published, meta.get('publication_period'), digest(' '.join(text.split())))
                                if dedup in seen:
                                    manifest[seen[dedup]]['aliases'].append(source)
                                    report['duplicates'] += 1
                                    continue
                                identifier = digest(json.dumps([meta['ticker'], str(path), locator, digest(text)], ensure_ascii=False))
                                seen[dedup] = identifier
                                manifest[identifier] = {**source, 'aliases': []}
                                record = dict(id=identifier, title=f"{meta['ticker']} | {meta['title']} | {locator}",
                                              text=text, **{k: source[k] for k in ('ticker', 'company', 'publication_date',
                                                                                 'publication_period', 'category', 'form', 'report_date', 'locator')})
                                out.write(json.dumps(record, ensure_ascii=False) + '\n')
                                report['segments'] += 1
                                report['characters'] += len(text)
                                companies[meta['ticker']] += 1
                                category_counts[meta['category']] += 1
                        except Exception as exc:
                            report['issues'].append(dict(path=str(path), reason='extraction_failed', error=str(exc)))
        report['companies'], report['categories'] = dict(companies), dict(category_counts)
        report['input_sha256'] = hashlib.sha256(staging.read_bytes()).hexdigest()
        report['estimated_text_tokens'] = round(report['characters'] / 4)
        report['token_estimate_note'] = 'Characters / 4 only; excludes prompts, extraction passes, summaries and outputs. Not an API cost estimate.'
        write_json(root / 'manifest.json', manifest)
        write_json(root / 'ingestion-report.json', report)
        if not report['segments']:
            raise ValueError(f'No usable text; inspect {root / "ingestion-report.json"}')
        staging.replace(root / 'input/documents.jsonl')
        return report
    finally:
        staging.unlink(missing_ok=True)
