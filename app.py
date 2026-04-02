"""Local UI. Run: .venv/bin/streamlit run app.py --server.address 127.0.0.1"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st
from ir_graphrag.calculations import calculate_change

PROJECT = Path(__file__).resolve().parent
WORKSPACES = PROJECT / 'workspaces'
st.set_page_config(page_title='上市公司 GraphRAG', page_icon='🔎', layout='wide')
st.title('上市公司 GraphRAG')
st.caption('财务原文检索与数值核算 · Microsoft GraphRAG 关系分析 · 可追溯的披露证据')


def run_command(command, root, *args):
    process = subprocess.run([sys.executable, '-m', 'ir_graphrag.cli', command, '--root', str(root), *args],
                             cwd=PROJECT, capture_output=True, text=True)
    if process.returncode:
        st.error(process.stderr[-2000:] or '操作失败，请查看工作区日志。')
        return False
    return True


with st.sidebar:
    st.header('资料库')
    workspaces = sorted((p for p in WORKSPACES.glob('*') if (p / 'settings.yaml').is_file()),
                        key=lambda p: (not (p / 'index-ready.json').exists(), p.name))
    selected = st.selectbox('选择资料库', workspaces, format_func=lambda p: p.name + (' · 图谱已建' if (p / 'index-ready.json').exists() else ' · 原文检索')) if workspaces else None
    with st.expander('导入新的资料库'):
        st.caption('新资料库独立建图。公司与时间范围在入库时确定。')
        name = st.text_input('资料库名称', placeholder='company-financials')
        datasets = st.text_area('数据目录（每行一个）')
        profile = st.selectbox('导入范围', ['financial', 'all'], format_func=lambda x: '财务披露（财报、业绩材料、投资者会议）' if x == 'financial' else '全部文档')
        categories = st.text_input('类别，可选（逗号分隔）', placeholder='quarterly_results,annual_report')
        since = st.text_input('发布日期起点，可选', placeholder='2025-01-01')
        until = st.text_input('发布日期终点，可选', placeholder='2025-12-31')
        limit = st.number_input('最多归档记录数（0 表示全部）', min_value=0, value=5)
        if st.button('导入文档'):
            if not name or Path(name).name != name or name in {'.', '..'}:
                st.error('请输入不含路径分隔符的资料库名称。')
            elif not datasets.strip():
                st.error('请输入数据目录。')
            else:
                target = WORKSPACES / name
                with st.spinner('提取文档并保留来源…'):
                    if run_command('init', target):
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
                            st.success('文档已导入，可直接进行财务原文问答；关系分析可进一步建立图谱。')
                            st.rerun()

if selected is None:
    st.info('从侧栏导入资料，或按 README 使用命令行创建资料库。')
    st.stop()
root = selected
report_file = root / 'ingestion-report.json'
report = json.loads(report_file.read_text()) if report_file.is_file() else {}
ready = root / 'index-ready.json'
counts = json.loads(ready.read_text())['counts'] if ready.is_file() else {}
cols = st.columns(4)
cols[0].metric('已导入片段（页 / 正文）', report.get('segments', 0))
cols[1].metric('知识实体', counts.get('entities', '待建图'))
cols[2].metric('实体关系', counts.get('relationships', '待建图'))
cols[3].metric('需核查的导入记录', len(report.get('issues', [])))
with st.expander('资料范围与导入质量', expanded=not ready.is_file()):
    st.json(report.get('scope', {}))
    st.write('公司：', report.get('companies', {}))
    st.write('类别：', report.get('categories', {}))
    st.caption('日期筛选使用发布日期，不推定未知日期。仅提取可读取文本；扫描页、图表图像和未转录视频可能不完整，请核查原件。')
    if report.get('issues'):
        st.dataframe(report['issues'], width='stretch')
    st.download_button('下载导入报告', json.dumps(report, ensure_ascii=False, indent=2), file_name='ingestion-report.json')

if not ready.is_file():
    st.info('财务原文问答可直接使用已导入材料。需要实体关系或全局主题分析时，再建立图谱。')
    with st.expander('建立关系图谱'):
        st.caption('调用 Chat 和 Embedding 生成实体、关系和社区报告；大资料库可能运行较久。')
        if st.button('建立 GraphRAG 索引', disabled=not report.get('segments')):
            with st.spinner('提取实体关系、生成社区报告与向量。进度见资料库 logs 目录…'):
                if run_command('index', root):
                    st.rerun()
if not report.get('segments'):
    st.stop()

methods = {'financial': '财务原文：精确金额、会计口径、财务期间',
           'local': '实体与关系：公司、产品、客户、风险', 'global': '全局分析：跨文档主题与业务趋势',
           'basic': '原文检索：财务数字、日期和具体披露', 'drift': '多步探索：复杂关系与进一步检索'}
method = st.selectbox('问答方式', list(methods) if ready.is_file() else ['financial'], format_func=lambda x: methods[x])
filter_args = []
if method == 'financial':
    with st.expander('限定公司、已披露日期和财务期间', expanded=True):
        tickers = st.multiselect('公司', sorted(report.get('companies', {})), key=f'tickers:{root}')
        as_of = st.text_input('仅使用截至此日已发布的资料（可选）', placeholder='2026-09-26')
        cols = st.columns(2)
        fiscal_year = cols[0].number_input('明确标注的财年（0 表示不限）', min_value=0, max_value=2200, value=0)
        fiscal_quarter = cols[1].selectbox('明确标注的财季', ['不限', '1', '2', '3', '4'])
        st.caption('截止日期按发布日期过滤。财年/财季仅匹配明确元数据：缺失字段的文档会被排除，未命中不代表该期间没有披露。不确定时保留“不限”，在问题中写明期间。')
        filter_args = [v for ticker in tickers for v in ('--ticker', ticker)]
        if as_of:
            filter_args += ['--as-of', as_of]
        if fiscal_year:
            filter_args += ['--fiscal-year', str(fiscal_year)]
        if fiscal_quarter != '不限':
            filter_args += ['--fiscal-quarter', fiscal_quarter]
else:
    st.caption('图谱分析采用整个资料库的建图范围；严格公司/时间筛选请使用财务原文模式或另建范围明确的图谱。')
question = st.text_area('你的问题', placeholder='2026 年第二季度销售额与上年同期各是多少？请区分季度和累计数，注明单位及来源。')
if st.button('查询', type='primary', disabled=not question.strip()):
    output = root / 'answers' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    with st.spinner('检索并生成回答…'):
        if run_command('ask', root, question, '--method', method, *filter_args, '--output', str(output)):
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
    st.caption(f"问题：{result['question']} · 检索方式：{result['method']}")
    st.markdown(result['answer'])
    audit = result.get('citation_audit', {})
    if audit.get('invalid_references'):
        st.error('回答含无法对应的引用：' + ', '.join(audit['invalid_references']))
    elif audit.get('status') == 'verified_ids':
        st.caption('引用编号已核对；这不代表每一项结论或财务口径已由程序核实。')
    elif audit.get('status') == 'no_citations':
        st.caption('这份回答没有可核对的引用编号。')
    if result.get('query_scope'):
        with st.expander('这份回答实际使用的筛选范围'):
            st.json(result['query_scope'])
    st.subheader('检索证据与原文定位')
    st.caption('这些是本次返回的检索材料。请按回答中的 [Data: ...] 编号对应核对；图谱背景材料不等于对每个结论的直接证明。')
    cited_only = st.checkbox('只显示回答实际引用的证据', value=bool(audit.get('references')))
    shown = [s for s in result['evidence'] if not cited_only or s.get('cited')]
    for item in shown[:50]:
        label = f"片段 {item['source_id']} · {item['ticker']} · {item['title'][:80]} · {item['locator']}"
        with st.expander(label):
            st.write('GraphRAG 记录：', ', '.join(item['references']))
            st.write('发布日期：', item['publication_date'] or '未知')
            st.write('财务期间：', {k: item.get(k) for k in ('fiscal_year', 'fiscal_quarter', 'report_date')})
            st.write('证据类型：', '原文片段' if item['evidence_kind'] == 'direct_text' else '图谱背景')
            if item['url'].startswith(('https://', 'http://')):
                st.link_button('查看来源网页', item['url'])
            st.code(item['source_path'], language=None)
            st.text(item['excerpt'])
    if len(result['evidence']) > 50:
        st.caption(f"界面展示前 50 条；完整 {len(result['evidence'])} 条证据可下载。")
    st.download_button('下载回答与全部证据', json.dumps(result, ensure_ascii=False, indent=2), file_name='answer.json', mime='application/json')
    with st.expander('引用原文数值，核算变化率 / 百分点 / 基点'):
        st.caption('数字必须完整出现在选定原文中，负号和括号不能省略。请自行确认指标、单位、期间与会计口径；程序只校验引用和算术，不代替会计判断。')
        metric = st.text_input('比较的指标', placeholder='Total sales')
        basis = st.text_input('会计口径', value='GAAP')
        operands = []
        for label, column in zip(('本期', '上期'), st.columns(2)):
            with column:
                source = st.selectbox(f'{label}证据', result['evidence'], format_func=lambda x: f"{x['source_id']} · {x['ticker']} · {x['locator']}", key=f'calc-source:{label}:{root}') if result['evidence'] else None
                if source is None:
                    continue
                value = st.text_input(f'{label}原文数值', placeholder='10,876 或 (2,480)')
                unit = st.selectbox(f'{label}单位', ['USD millions', 'USD thousands', 'USD', 'USD billions', 'USD per share', 'percent'])
                period = st.text_input(f'{label}财务期间', placeholder='Three months ended 2026-06-30')
                quote = st.text_area(f'{label}原文引用（包含完整数值）', value=source['excerpt'], height=120, key=f'calc-quote:{label}:{root}:{source["source_id"]}')
                operands.append(dict(source_id=source['source_id'], value=value, unit=unit, period=period, quote=quote, metric=metric, basis=basis))
        if st.button('用 Decimal 核算', disabled=len(operands) != 2):
            try:
                calculated = calculate_change(*operands, result['evidence'])
                st.write('差额：', calculated['difference'], calculated['difference_unit'])
                if calculated['difference_basis_points'] is not None:
                    st.write('基点变化：', calculated['difference_basis_points'])
                st.write('变化率：', calculated['growth_percent'] + '%' if calculated['growth_percent'] is not None else '上期为零或负数，不给出易误导的增长率')
                st.download_button('下载核算与操作数来源', json.dumps(calculated, ensure_ascii=False, indent=2), file_name='calculation.json')
            except ValueError as exc:
                st.error(str(exc))
