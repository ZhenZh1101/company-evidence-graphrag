"""Local UI. Run: .venv/bin/streamlit run app.py --server.address 127.0.0.1"""
import json
import subprocess
import sys
from datetime import datetime
from html import escape
from pathlib import Path

import streamlit as st
from ir_graphrag.calculations import calculate_change
from ir_graphrag.engine import PROVIDERS
from ir_graphrag.i18n import LANGUAGES, normalize_language, translate

PROJECT = Path(__file__).resolve().parent
WORKSPACES = PROJECT / 'workspaces'
language = normalize_language(st.session_state.get('language', st.query_params.get('lang')))
st.session_state['language'] = language
# Refresh selected option text as well as labels when the locale changes.
if st.session_state.get('_rendered_language') != language:
    for key in list(st.session_state):
        if key in {'workspace', 'import_profile'} or key.startswith(('method:', 'fiscal_quarter:', 'calc-unit:')):
            st.session_state[key] = st.session_state[key]
st.session_state['_rendered_language'] = language


def t(message, **values):
    return translate(message, language, **values)


st.set_page_config(page_title=t('Public Company GraphRAG'), page_icon='🔎', layout='wide', initial_sidebar_state='auto')
st.html('''<style>
:root { --ink: #182936; --muted: #677783; --line: #dce4e8; --accent: #176b65; --paper: #fff; --bg: #f5f7f8; }
.stApp { background: var(--bg); color: var(--ink); }
.stApp, .stApp p, .stApp h1, .stApp h2, .stApp h3, .stApp input, .stApp textarea, .stApp button { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }
[data-testid="stHeader"] { background: transparent; height: 0 !important; z-index: 999993; pointer-events: none; }
[data-testid="stToolbarActions"], [data-testid="stAppDeployButton"], [data-testid="stMainMenu"], [data-testid="stDecoration"] { display: none; }
[data-testid="stExpandSidebarButton"] { position: fixed; top: 92px; left: 8px; pointer-events: auto; background: white; border: 1px solid var(--line); }
.research-header { position: fixed; inset: 0 0 auto; height: 88px; z-index: 999992; background: white; border-bottom: 1px solid var(--line); display: flex; align-items: center; gap: 13px; padding: 20px 36px; }
.brand-mark { width: 37px; height: 37px; display: grid; place-items: center; border-radius: 10px; background: var(--accent); color: white; font-size: 18px; font-weight: 750; }
.brand-name { font-size: 18px; font-weight: 700; letter-spacing: .3px; }
.brand-subtitle { font-size: 12px; color: var(--muted); margin-top: 2px; }
.st-key-language-control { position: fixed; top: 23px; right: 36px; width: 145px; z-index: 999993; }
.research-status { position: fixed; right: 205px; top: 34px; z-index: 999993; font-size: 13px; color: var(--muted); display: flex; gap: 8px; align-items: center; }
[data-testid="stElementContainer"]:has(> .stHtml > .research-header), [data-testid="stElementContainer"]:has(> .stHtml > .research-status), [data-testid="stLayoutWrapper"]:has(> .st-key-language-control) { position: absolute; }
.status-dot { width: 7px; height: 7px; border-radius: 50%; background: #22866a; }
[data-testid="stSidebar"] { background: var(--bg); border-right: 1px solid var(--line); min-width: 292px; max-width: 292px; }
[data-testid="stSidebarContent"] { padding: 0; }
[data-testid="stSidebarUserContent"] { padding: 108px 22px 32px; }
[data-testid="stSidebarHeader"] { position: absolute; top: 88px; right: 0; height: 28px; padding: 0 5px; }
.stMainBlockContainer { max-width: 1180px; padding: 124px 44px 56px; }
.stMainBlockContainer > [data-testid="stVerticalBlock"] { gap: 18px; }
.eyebrow { font-size: 11px; letter-spacing: 1.6px; color: var(--accent); text-transform: uppercase; font-weight: 700; }
h1 { font-size: 30px !important; line-height: 1.4 !important; letter-spacing: -.5px; padding: 0 !important; }
h2 { font-size: 19px !important; letter-spacing: -.2px; }
h3 { font-size: 15px !important; }
p, li { line-height: 1.7; }
[data-testid="stCaptionContainer"] { color: var(--muted); font-size: 12px; }
.lede { color: var(--muted); font-size: 14px; margin: -6px 0 4px; line-height: 1.7; }
[data-testid="stSidebar"] [data-testid="stMetric"] { border: 1px solid var(--line); border-radius: 10px; padding: 12px; background: white; min-height: 96px; }
[data-testid="stSidebar"] [data-testid="stMetricValue"] { font-size: 24px; font-weight: 650; }
[data-testid="stSidebar"] [data-testid="stMetricLabel"] p { color: var(--muted); font-size: 11px; }
[data-testid="stSidebar"] [data-testid="stMetricLabel"] { height: auto; }
[data-testid="stSidebar"] [data-testid="stMetricLabel"] * { white-space: normal; overflow: visible; }
.st-key-library-summary [data-testid="stColumn"] { min-width: 0; }
.scope-note { font-size: 12px; color: var(--muted); line-height: 1.7; border-top: 1px solid var(--line); padding-top: 16px; margin-top: 10px; }
[data-testid="stWidgetLabel"] p { font-size: 12px; font-weight: 550; }
[data-baseweb="select"] > div, [data-baseweb="input"], [data-baseweb="base-input"], [data-testid="stNumberInputContainer"], [data-testid="stTextArea"] textarea { background-color: white; border-color: var(--line); }
[data-baseweb="tag"] { background-color: #edf6f2; color: var(--accent); }
[data-testid="stExpander"] { border-color: var(--line); border-radius: 10px; background: white; }
[data-testid="stExpander"] summary p { font-size: 13px; }
button[kind="primary"] { background: var(--accent); border-color: var(--accent); border-radius: 8px; font-weight: 650; }
button[kind="primary"]:hover { background: #115650; border-color: #115650; }
button[kind="primary"]:disabled { background: var(--accent); border-color: var(--accent); color: white; opacity: .45; }
button[kind="secondary"], [data-testid="stDownloadButton"] button, [data-testid="stLinkButton"] a { border-color: var(--line); border-radius: 7px; background: white; font-size: 12px; }
button:focus-visible, a:focus-visible { outline: 3px solid #76bbae !important; outline-offset: 3px; }
.st-key-question-composer { background: white; border: 1px solid #c9d8dc; border-radius: 14px; padding: 18px 20px 14px; box-shadow: 0 4px 14px #16323b06; }
.st-key-question-composer [data-testid="stTextArea"] textarea { border: 0; padding: 8px 0; font-size: 15px; line-height: 1.75; min-height: 122px; }
.st-key-question-composer [data-testid="stTextArea"] > div { border: 0; box-shadow: none; }
.st-key-question-composer [data-testid="stTextArea"] > div:focus-within { outline: 2px solid #76bbae; outline-offset: 4px; }
.st-key-question-composer [data-testid="stWidgetLabel"] p { font-size: 13px; font-weight: 650; }
.st-key-question-composer [data-testid="stHorizontalBlock"] { border-top: 1px solid #e9eef0; padding-top: 12px; align-items: center; }
.st-key-question-composer [data-testid="stCaptionContainer"] p { font-size: 12px; }
.st-key-examples button { color: #576974; background: transparent; }
.st-key-examples button:hover { background: white; color: var(--accent); border-color: #a8c6bf; }
.research-steps { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; margin: 20px 0; padding: 28px 0; border-top: 1px solid var(--line); }
.research-step span { display: block; color: var(--accent); font: 11px ui-monospace, SFMono-Regular, monospace; margin-bottom: 10px; }
.research-step b { display: block; font-size: 13px; margin-bottom: 5px; }
.research-step p { font-size: 12px; color: var(--muted); margin: 0; }
.st-key-answer-card { background: white; border: 1px solid var(--line); border-radius: 12px; padding: 24px; }
.st-key-answer-card [data-testid="stMarkdownContainer"] { font-size: 15px; line-height: 1.9; overflow-wrap: anywhere; }
.st-key-answer-card table { display: block; max-width: 100%; overflow-x: auto; }
.st-key-answer-card th { background: #edf6f2; }
[class*="st-key-source-card-"] { background: white; border: 1px solid var(--line); border-radius: 10px; padding: 18px; height: 100%; }
.source-top { display: flex; gap: 9px; flex-wrap: wrap; align-items: center; font-size: 11px; color: var(--muted); margin-bottom: 9px; }
.source-label { color: var(--accent); font-weight: 700; }
.source-company { color: var(--ink); font-weight: 650; }
.source-title { color: var(--ink); font-size: 13px; font-weight: 650; line-height: 1.6; overflow-wrap: anywhere; }
.source-location { font-size: 11px; color: var(--muted); margin-top: 6px; overflow-wrap: anywhere; }
.source-quote { border-left: 2px solid #9fc6ba; padding-left: 11px; color: #3e5961; font-size: 12px; line-height: 1.8; white-space: pre-wrap; overflow-wrap: anywhere; margin: 14px 0 0; }
.footer-note { font-size: 11px; color: var(--muted); margin-top: 24px; }
@media (max-width: 1100px) {
  .stMainBlockContainer { padding-left: 28px; padding-right: 28px; }
  .brand-subtitle { max-width: 340px; }
  .st-key-source-grid > [data-testid="stHorizontalBlock"] { flex-wrap: wrap; }
  .st-key-source-grid > [data-testid="stHorizontalBlock"] > [data-testid="stColumn"] { min-width: 100%; }
}
@media (max-width: 680px) {
  .research-header { height: 80px; padding: 16px 20px; }
  .brand-name { font-size: 16px; }
  .brand-subtitle { display: none; }
  .st-key-language-control { top: 20px; right: 16px; width: 120px; }
  .research-status { display: none; }
  [data-testid="stSidebarUserContent"] { padding-top: 100px; }
  [data-testid="stExpandSidebarButton"] { top: 86px; }
  .stMainBlockContainer { padding: 126px 20px 36px; }
  h1 { font-size: 25px !important; }
  .research-steps { grid-template-columns: 1fr; gap: 18px; }
  .st-key-answer-card { padding: 18px; }
  .st-key-question-composer { padding: 16px; }
}
</style>''')
st.html(f'''<header class="research-header"><div class="brand-mark" aria-hidden="true">G</div>
    <div><div class="brand-name">GraphRAG</div><div class="brand-subtitle">{escape(t('Research workspace · Archived documents, traceable answers'))}</div></div></header>''')
with st.container(key='language-control'):
    language = st.selectbox('Language / 语言', list(LANGUAGES), format_func=LANGUAGES.get, key='language',
                            label_visibility='collapsed', help=t('Applies to the interface and new answers. Source text and saved answers keep their original language.'))
if st.query_params.get('lang') != language:
    st.query_params['lang'] = language
st.html(f'<div class="eyebrow">{escape(t("Evidence-based research"))}</div>')
st.title(t('Public Company GraphRAG'))
st.html(f'<p class="lede">{escape(t("Search company disclosures. Ask precise questions. Verify the original evidence."))}</p>')


def run_command(command, root, *args):
    process = subprocess.run([sys.executable, '-m', 'ir_graphrag.cli', command, '--root', str(root), *args],
                             cwd=PROJECT, capture_output=True, text=True)
    if process.returncode:
        st.error(process.stderr[-2000:] or t('Operation failed. Check the workspace logs.'))
        return False
    return True


with st.sidebar:
    st.html(f'<div class="eyebrow">{escape(t("Document library"))}</div>')
    workspaces = sorted((p for p in WORKSPACES.glob('*') if (p / 'settings.yaml').is_file()),
                        key=lambda p: (not (p / 'index-ready.json').exists(), p.name))
    selected = st.selectbox(t('Select workspace'), workspaces, format_func=lambda p: p.name, key='workspace') if workspaces else None
    library_summary = st.container(key='library-summary')
    research_scope = st.container()
    library_tools = st.container()
    with st.expander(t('Import a new workspace')):
        st.caption(t('Each workspace has its own graph. Company and date scope are set during import.'))
        name = st.text_input(t('Workspace name'), placeholder='company-financials', key='import_name')
        datasets = st.text_area(t('Data directories (one per line)'), key='import_datasets')
        profile = st.selectbox(t('Import scope'), ['financial', 'all'], format_func=lambda x: t('Financial disclosures (reports, earnings materials, investor events)') if x == 'financial' else t('All documents'), key='import_profile')
        categories = st.text_input(t('Categories, optional (comma-separated)'), placeholder='quarterly_results,annual_report', key='import_categories')
        since = st.text_input(t('Publication start date, optional'), placeholder='2025-01-01', key='import_since')
        until = st.text_input(t('Publication end date, optional'), placeholder='2025-12-31', key='import_until')
        limit = st.number_input(t('Maximum archive records (0 for all)'), min_value=0, value=5, key='import_limit')
        provider_names = {'openclaw': 'OpenClaw', 'openai': 'OpenAI', 'zai': 'Z.ai', 'deepseek': 'DeepSeek'}
        provider = st.selectbox(t('Chat API provider'), list(PROVIDERS), format_func=provider_names.get, key='init-provider')
        defaults = PROVIDERS[provider]
        model = st.text_input(t('Chat model'), value=defaults['model'], key=f'init-model:{provider}')
        api_base = st.text_input(t('Chat API URL'), value=defaults['api_base'], key=f'init-api-base:{provider}')
        embedding_provider = st.selectbox(t('Embedding provider (used for graph indexing)'), ['openclaw', 'openai'],
                                          index=0 if provider == 'openclaw' else 1,
                                          format_func=provider_names.get, key=f'init-embedding-provider:{provider}')
        embedding_defaults = PROVIDERS[embedding_provider]
        embedding_model = st.text_input(t('Embedding model'), value=embedding_defaults['embedding_model'],
                                        key=f'init-embedding-model:{provider}:{embedding_provider}')
        embedding_api_base = st.text_input(t('Embedding API URL'), value=embedding_defaults['api_base'],
                                           key=f'init-embedding-api-base:{provider}:{embedding_provider}')
        vector_size = st.number_input(t('Embedding dimensions'), min_value=1, value=embedding_defaults['vector_size'],
                                       key=f'init-vector-size:{provider}:{embedding_provider}')
        st.caption(t('Set keys in the workspace .env or environment: {chat_key} for Chat; {embedding_key} for Embedding. Financial source Q&A only needs the Chat key.',
                     chat_key=defaults['api_key_env'], embedding_key=embedding_defaults['api_key_env']))
        if 'openclaw' in (provider, embedding_provider):
            st.caption(t('OpenClaw can also read the gateway token through OPENCLAW_CONFIG.'))
        if st.button(t('Import documents')):
            if not name or Path(name).name != name or name in {'.', '..'}:
                st.error(t('Enter a workspace name without path separators.'))
            elif not datasets.strip():
                st.error(t('Enter a data directory.'))
            else:
                target = WORKSPACES / name
                with st.spinner(t('Extracting documents and preserving sources…')):
                    if run_command('init', target, '--provider', provider, '--model', model, '--api-base', api_base,
                                   '--embedding-provider', embedding_provider, '--embedding-model', embedding_model,
                                   '--embedding-api-base', embedding_api_base, '--vector-size', str(vector_size)):
                        args = ['--profile', profile, *[v for line in datasets.splitlines() if line.strip() for v in ('--dataset', line.strip())]]
                        for category in categories.split(','):
                            if category.strip():
                                args += ['--category', category.strip()]
                        if since:
                            args += ['--since', since]
                        if until:
                            args += ['--until', until]
                        if limit:
                            args += ['--limit-records', str(limit)]
                        if run_command('prepare', target, *args):
                            st.success(t('Documents imported. Financial source Q&A is ready; build a graph for relationship analysis.'))
                            st.rerun()

if selected is None:
    st.info(t('Import documents from the sidebar, or create a workspace with the CLI as described in the README.'))
    st.stop()
root = selected
report_file = root / 'ingestion-report.json'
report = json.loads(report_file.read_text()) if report_file.is_file() else {}
ready = root / 'index-ready.json'
counts = json.loads(ready.read_text())['counts'] if ready.is_file() else {}
st.html(f'<div class="research-status" role="status"><span class="status-dot"></span>{escape(t("Graph ready" if ready.is_file() else "Source search"))}</div>')
with library_summary:
    cols = st.columns(2, wrap=False)
    cols[0].metric(t('Documents loaded'), f"{report.get('records_selected', 0):,}")
    cols[1].metric(t('Source passages'), f"{report.get('segments', 0):,}")
with library_tools.expander(t('Data scope and import quality'), expanded=not report.get('segments')):
    st.metric(t('Graph entities / relationships'), f"{counts['entities']} / {counts['relationships']}" if counts else t('Not built'))
    st.metric(t('Import records needing review'), len(report.get('issues', [])))
    st.json(report.get('scope', {}))
    st.write(t('Companies:'), report.get('companies', {}))
    st.write(t('Categories:'), report.get('categories', {}))
    st.caption(t('Date filters use publication dates; unknown dates are not inferred. Only readable text is extracted. Scanned pages, chart images and untranscribed videos may be incomplete; check the originals.'))
    if report.get('issues'):
        st.dataframe(report['issues'], width='stretch')
    st.download_button(t('Download import report'), json.dumps(report, ensure_ascii=False, indent=2), file_name='ingestion-report.json', mime='application/json')

if not ready.is_file():
    with library_tools.expander(t('Build a relationship graph')):
        st.caption(t('Financial source Q&A can use imported materials immediately. Build a graph for entity relationships or global topic analysis.'))
        st.caption(t('Uses Chat and Embedding to generate entities, relationships and community reports. Large workspaces may take a long time.'))
        if st.button(t('Build GraphRAG index'), disabled=not report.get('segments')):
            with st.spinner(t('Extracting entities and relationships, generating community reports and embeddings. See workspace logs for progress…')):
                if run_command('index', root):
                    st.rerun()
if not report.get('segments'):
    st.stop()

methods = {'financial': t('Financial sources: exact amounts, accounting basis and fiscal periods'),
           'local': t('Entities and relationships: companies, products, customers and risks'), 'global': t('Global analysis: topics and business trends across documents'),
           'basic': t('Source search: financial figures, dates and specific disclosures'), 'drift': t('Multi-step exploration: complex relationships and further retrieval')}
method_labels = {'financial': t('Financial sources'), 'local': t('Entities & relationships'),
                 'global': t('Global analysis'), 'basic': t('Source search'), 'drift': t('Multi-step exploration')}
with research_scope:
    st.subheader(t('Search scope'))
    method = st.selectbox(t('Search method'), list(methods) if ready.is_file() else ['financial'], format_func=method_labels.get,
                         help='\n\n'.join(methods.values()), key=f'method:{root}')
filter_args = []
if method == 'financial':
    with research_scope:
        tickers = st.multiselect(t('Companies'), sorted(report.get('companies', {})),
                                placeholder=t('Choose companies'), key=f'tickers:{root}')
        st.caption(t('Leave companies unselected to search the entire workspace.'))
        as_of = st.text_input(t('Only use materials published on or before this date (optional)'), placeholder='2026-09-26', key=f'as_of:{root}')
        with st.expander(t('Fiscal period')):
            fiscal_year = st.number_input(t('Explicit fiscal year (0 for any)'), min_value=0, max_value=2200, value=0, key=f'fiscal_year:{root}')
            fiscal_quarter = st.selectbox(t('Explicit fiscal quarter'), ['all', '1', '2', '3', '4'], format_func=lambda x: t('Any') if x == 'all' else x, key=f'fiscal_quarter:{root}')
            st.caption(t('The cutoff filters publication dates. Fiscal year/quarter filters only match explicit metadata and exclude documents with missing fields. No matches does not mean no disclosures exist. If unsure, leave these unrestricted and specify the period in your question.'))
        st.html(f'<p class="scope-note">{escape(t("Date filters use publication dates, not financial reporting periods. Undated documents are excluded when a cutoff is set."))}</p>')
        filter_args = [v for ticker in tickers for v in ('--ticker', ticker)]
        if as_of:
            filter_args += ['--as-of', as_of]
        if fiscal_year:
            filter_args += ['--fiscal-year', str(fiscal_year)]
        if fiscal_quarter != 'all':
            filter_args += ['--fiscal-quarter', fiscal_quarter]
else:
    research_scope.caption(t('Graph analysis uses the entire indexed workspace. For strict company/date filters, use financial source mode or build a separately scoped graph.'))

with st.expander(t('How this research workspace works')):
    st.write(t('Search financial disclosures for exact figures, periods and accounting bases. In graph-ready workspaces, explore entities, relationships and themes across documents.'))
    st.caption(t('Financial source search and calculations · Microsoft GraphRAG relationship analysis · Traceable disclosure evidence'))
with st.container(key='question-composer'):
    question = st.text_area(t('Your question'), placeholder=t('What were sales in Q2 2026 and the prior-year quarter? Distinguish quarterly and year-to-date amounts, and include units and sources.'), key=f'question:{root}', height=150)
    hint, action = st.columns([3, 1], vertical_alignment='center')
    hint.caption(t('Specify the company, period and metric to help locate evidence.'))
    ask = action.button(t('Search and answer ↗'), type='primary', disabled=not question.strip(), key='ask', width='stretch')
with st.container(key='examples', horizontal=True):
    for label, prompt in [
        (t('Revenue & growth'), t('What was revenue in the latest reported quarter and the prior-year quarter? Distinguish quarterly and year-to-date figures, and cite the sources.')),
        (t('Margins & profitability'), t('How did operating margin change in the latest reported quarter? Distinguish GAAP and adjusted measures, and cite the sources.')),
        (t('Business risks'), t('What are the main business risks disclosed in the latest available reports? State the reporting periods and cite the original evidence.')),
    ]:
        st.button(label, on_click=st.session_state.update, args=({f'question:{root}': prompt},))
if ask:
    output = root / 'answers' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    with st.spinner(t('Retrieving evidence and generating an answer…')):
        if run_command('ask', root, question, '--method', method, '--language', language, *filter_args, '--output', str(output)):
            st.session_state['answer'] = (str(root), json.loads(output.read_text()))
if st.session_state.get('answer', (None,))[0] != str(root):
    saved = sorted((root / 'answers').glob('*.json'), key=lambda p: p.stat().st_mtime)
    for path in reversed(saved):
        candidate = json.loads(path.read_text())
        if candidate.get('answer') and candidate.get('question') and candidate.get('method'):
            st.session_state['answer'] = (str(root), candidate)
            break
if st.session_state.get('answer', (None,))[0] == str(root):
    result = st.session_state['answer'][1]
    st.subheader(t('Answer'))
    st.caption(t('Question: {question} · Search method: {method}', question=result['question'], method=result['method']))
    with st.container(key='answer-card'):
        st.markdown(result['answer'])
        st.divider()
        st.caption(t('Check the sources below for figures, reporting periods and accounting bases.'))
    audit = result.get('citation_audit', {})
    if audit.get('invalid_references'):
        st.error(t('The answer contains unmatched references: {references}', references=', '.join(audit['invalid_references'])))
    elif audit.get('status') == 'verified_ids':
        st.caption(t('Citation IDs have been checked. This does not verify every conclusion or accounting basis.'))
    elif audit.get('status') == 'no_citations':
        st.caption(t('This answer has no citation IDs to check.'))
    if result.get('query_scope'):
        with st.expander(t('Filters actually used for this answer')):
            st.json(result['query_scope'])
    st.subheader(t('Retrieved evidence and source locations'))
    st.caption(t('These are the retrieved materials. Match them to the [Data: ...] IDs in the answer. Graph background is not direct proof of every conclusion.'))
    cited_only = st.checkbox(t('Only show evidence cited in the answer'), value=bool(audit.get('references')), key=f'cited_only:{root}:{result["question"]}')
    shown = [s for s in result['evidence'] if not cited_only or s.get('cited')]
    with st.container(key='source-grid'):
        for index, item in enumerate(shown[:50]):
            if index % 2 == 0:
                source_columns = st.columns(2)
            with source_columns[index % 2].container(key=f'source-card-{index}'):
                excerpt = item['excerpt']
                preview = excerpt[:360] + ('…' if len(excerpt) > 360 else '')
                st.html(f'''<div class="source-top"><span class="source-label">[{escape(str(item['source_id']))}]</span>
                    <span class="source-company">{escape(item['ticker'])}</span><span>{escape(item['publication_date'] or t('Unknown'))}</span></div>
                    <div class="source-title">{escape(item['title'])}</div>
                    <div class="source-location">{escape(item['locator'])} · {escape(t('Source passage') if item['evidence_kind'] == 'direct_text' else t('Graph background'))}</div>
                    <blockquote class="source-quote">{escape(preview)}</blockquote>''')
                if item['url'].startswith(('https://', 'http://')):
                    st.link_button(t('Open source webpage'), item['url'])
                with st.expander(t('View source passage and details')):
                    st.write(t('GraphRAG records:'), ', '.join(item['references']))
                    st.write(t('Fiscal period:'), {k: item.get(k) for k in ('fiscal_year', 'fiscal_quarter', 'report_date')})
                    st.code(item['source_path'], language=None)
                    st.text(excerpt)
    if not shown:
        st.caption(t('No evidence matches this view. Turn off the citation filter to see all retrieved sources.') if result['evidence'] else t('No source evidence was retrieved for this answer.'))
    if len(shown) > 50:
        st.caption(t('Showing the first 50 items. Download all {count} evidence items below.', count=len(result['evidence'])))
    st.download_button(t('Download answer and all evidence'), json.dumps(result, ensure_ascii=False, indent=2), file_name='answer.json', mime='application/json')
    with st.expander(t('Calculate change / percentage points / basis points from cited values')):
        st.caption(t('Values must appear in full in the selected source, including minus signs and parentheses. Confirm the metric, units, periods and accounting basis yourself. The program checks citations and arithmetic, not accounting judgments.'))
        metric = st.text_input(t('Metric to compare'), placeholder=t('Total sales'), key=f'calc-metric:{root}')
        basis = st.text_input(t('Accounting basis'), value='GAAP', key=f'calc-basis:{root}')
        operands = []
        for operand, column in zip(('current', 'previous'), st.columns(2)):
            label = t('Current period') if operand == 'current' else t('Previous period')
            with column:
                source = st.selectbox(t('{period} evidence', period=label), result['evidence'], format_func=lambda x: f"{x['source_id']} · {x['ticker']} · {x['locator']}", key=f'calc-source:{operand}:{root}') if result['evidence'] else None
                if source is None:
                    continue
                value = st.text_input(t('{period} source value', period=label), placeholder=t('10,876 or (2,480)'), key=f'calc-value:{operand}:{root}')
                unit = st.selectbox(t('{period} unit', period=label), ['USD millions', 'USD thousands', 'USD', 'USD billions', 'USD per share', 'percent'], format_func=t, key=f'calc-unit:{operand}:{root}')
                period = st.text_input(t('{period} fiscal period', period=label), placeholder=t('Three months ended 2026-06-30'), key=f'calc-period:{operand}:{root}')
                quote = st.text_area(t('{period} quote (including the full value)', period=label), value=source['excerpt'], height=120, key=f'calc-quote:{operand}:{root}:{source["source_id"]}')
                operands.append(dict(source_id=source['source_id'], value=value, unit=unit, period=period, quote=quote, metric=metric, basis=basis))
        if st.button(t('Calculate with Decimal'), disabled=len(operands) != 2):
            try:
                calculated = calculate_change(*operands, result['evidence'])
                st.write(t('Difference:'), calculated['difference'], t(calculated['difference_unit']))
                if calculated['difference_basis_points'] is not None:
                    st.write(t('Basis point change:'), calculated['difference_basis_points'])
                st.write(t('Percentage change:'), calculated['growth_percent'] + '%' if calculated['growth_percent'] is not None else t('The previous value is zero or negative; a growth rate would be misleading.'))
                st.download_button(t('Download calculation and operand sources'), json.dumps(calculated, ensure_ascii=False, indent=2), file_name='calculation.json', mime='application/json')
            except ValueError as exc:
                st.error(str(exc))
else:
    steps = [
        ('01', t('Scope'), t('Set your research scope'), t('Choose a workspace, companies and publication cutoff to focus your research.')),
        ('02', t('Retrieve'), t('Ask a precise question'), t('Search source passages for financial facts, or use a ready graph to explore relationships.')),
        ('03', t('Verify'), t('Check the original evidence'), t('Follow citation IDs to the original passages, and calculate changes from cited values.')),
    ]
    st.html('<div class="research-steps">' + ''.join(
        f'<div class="research-step"><span>{number} / {escape(label.upper())}</span><b>{escape(title)}</b><p>{escape(description)}</p></div>'
        for number, label, title, description in steps) + '</div>')
st.html(f'<p class="footer-note">{escape(t("Answers are grounded in the selected workspace. Verify material conclusions against the original disclosures."))}</p>')
