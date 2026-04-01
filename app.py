"""Local UI. Run: .venv/bin/streamlit run app.py --server.address 127.0.0.1"""
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import streamlit as st

PROJECT = Path(__file__).resolve().parent
WORKSPACES = PROJECT / 'workspaces'
st.set_page_config(page_title='上市公司 GraphRAG', page_icon='🔎', layout='wide')
st.title('上市公司 GraphRAG')
st.caption('Microsoft GraphRAG · 原始文档 → 实体关系 → 社区摘要 → 带证据的回答')


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
    selected = st.selectbox('选择资料库', workspaces, format_func=lambda p: p.name + (' · 可问答' if (p / 'index-ready.json').exists() else ' · 待建图')) if workspaces else None
    with st.expander('导入新的资料库'):
        st.caption('新资料库独立建图。公司与时间范围在入库时确定。')
        name = st.text_input('资料库名称', placeholder='company-financials')
        datasets = st.text_area('数据目录（每行一个）')
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
                        args = [v for line in datasets.splitlines() if line.strip() for v in ('--dataset', line.strip())]
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
                            st.success('文档已导入，请选择资料库后建立索引。')
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
    st.info('文档已导入。建立索引将调用配置的 Chat 和 Embedding 模型；大资料库可能运行较久。')
    if st.button('建立 GraphRAG 索引', type='primary', disabled=not report.get('segments')):
        with st.spinner('提取实体关系、生成社区报告与向量。进度见资料库 logs 目录…'):
            if run_command('index', root):
                st.rerun()
    st.stop()

methods = {'local': '实体与关系：公司、产品、客户、风险', 'global': '全局分析：跨文档主题与业务趋势',
           'basic': '原文检索：财务数字、日期和具体披露', 'drift': '多步探索：复杂关系与进一步检索'}
method = st.selectbox('问答方式', list(methods), format_func=lambda x: methods[x])
st.caption('精确财务数字优先选原文检索；回答中的金额、单位和期间仍应对照原始文件。')
question = st.text_area('你的问题', placeholder='这家公司披露了哪些主要业务风险？请给出来源。')
if st.button('查询', type='primary', disabled=not question.strip()):
    output = root / 'answers' / (datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.json')
    with st.spinner('检索并生成回答…'):
        if run_command('ask', root, question, '--method', method, '--output', str(output)):
            st.session_state['answer'] = (str(root), json.loads(output.read_text()))
if st.session_state.get('answer', (None,))[0] != str(root):
    saved = sorted((root / 'answers').glob('*.json'), key=lambda p: p.stat().st_mtime)
    if saved:
        st.session_state['answer'] = (str(root), json.loads(saved[-1].read_text()))
if st.session_state.get('answer', (None,))[0] == str(root):
    result = st.session_state['answer'][1]
    st.caption(f"问题：{result['question']} · 检索方式：{result['method']}")
    st.markdown(result['answer'])
    st.subheader('检索证据与原文定位')
    st.caption('这些是本次返回的检索材料。请按回答中的 [Data: ...] 编号对应核对；图谱背景材料不等于对每个结论的直接证明。')
    for item in result['evidence'][:50]:
        label = f"片段 {item['source_id']} · {item['ticker']} · {item['title'][:80]} · {item['locator']}"
        with st.expander(label):
            st.write('GraphRAG 记录：', ', '.join(item['references']))
            st.write('发布日期：', item['publication_date'] or '未知')
            st.write('证据类型：', '原文片段' if item['evidence_kind'] == 'direct_text' else '图谱背景')
            if item['url'].startswith(('https://', 'http://')):
                st.link_button('查看来源网页', item['url'])
            st.code(item['source_path'], language=None)
            st.text(item['excerpt'])
    if len(result['evidence']) > 50:
        st.caption(f"界面展示前 50 条；完整 {len(result['evidence'])} 条证据可下载。")
    st.download_button('下载回答与全部证据', json.dumps(result, ensure_ascii=False, indent=2), file_name='answer.json', mime='application/json')
