# 上市公司 GraphRAG

基于 [Microsoft GraphRAG](https://github.com/microsoft/graphrag/tree/v3.2.0) **3.2.0** 的本地文档研究系统。直接调用上游 `build_index` / `local_search` / `global_search` / `basic_search` / `drift_search`，生成实体关系、社区报告、LanceDB 向量和原文引用。

当前默认接入用户指定的 OpenClaw：

- Chat：`http://127.0.0.1:18789/v1/chat/completions`
- Embedding：`http://127.0.0.1:18789/v1/embeddings`
- 两者模型名：`openclaw/llm-gpt55`
- Embedding 维度：**1536**，通过真实接口探测确认；更换模型需重新探测并重建索引。
- 运行时读取 `~/.openclaw/openclaw.json` 的 `gateway.auth.token`。也可在资料库 `.env` 设置 `GRAPHRAG_API_KEY` 或 `OPENCLAW_CONFIG`。不复制、不提交网关密钥。

## 启动界面

安装依赖后，在项目根目录运行：

```bash
.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

打开 <http://127.0.0.1:8501>。选择资料库，查看导入范围；已建图的资料库可直接提问，未建图的资料库可点击“建立 GraphRAG 索引”。侧栏支持输入一个或多个本地文档目录建立新资料库。

资料库状态以各自的 `ingestion-report.json` 和 `index-ready.json` 为准：**已导入文本不代表已完成图谱索引**。

## 安装与命令行

需要 Python 3.11–3.13。系统 Python 3.9 不适用。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

若只安装直接依赖且允许重新解析传递依赖，可用 `.venv/bin/python -m pip install -e .`。锁文件包含此次实际验证的版本。

新建财务资料库前，将 `WORKSPACE` 设为资料库路径、`DATASET` 设为本地数据目录、`TICKER` 设为公司代码、`PERIOD` 设为需要查询的财务期间。命令不修改原始数据：

```bash
.venv/bin/ir-graphrag init --root "$WORKSPACE"
.venv/bin/ir-graphrag prepare --root "$WORKSPACE" \
  --dataset "$DATASET" --ticker "$TICKER" \
  --category quarterly_results --category annual_report
.venv/bin/ir-graphrag doctor --root "$WORKSPACE"
.venv/bin/ir-graphrag index --root "$WORKSPACE"
.venv/bin/ir-graphrag ask --root "$WORKSPACE" \
  "${TICKER} ${PERIOD} 营收是多少？同比变化如何？请注明单位、期间、计算依据和来源。" \
  --method basic --output "$WORKSPACE/answers/revenue.json"
```

全量语料包含大量技术论文、官网资料和申报文件；图谱提取需要多轮模型调用，可能运行很久。先通过 `status` 查看语料规模，再决定范围。报告中的 token 数只是字符数除以 4，**不是计费估算**；上游对自定义模型记录的零美元成本不表示免费。

跨公司比较：将 `COMPARISON_WORKSPACE` 设为另一个新资料库路径，`OTHER_DATASET` 设为另一家公司数据目录；在 `prepare` 中重复 `--dataset`。公司代码从各目录归档元数据读取，实体与每个文本块保留公司信息。日期变量 `SINCE` 与 `UNTIL` 使用 `YYYY-MM-DD` 格式：

```bash
.venv/bin/ir-graphrag init --root "$COMPARISON_WORKSPACE"
.venv/bin/ir-graphrag prepare --root "$COMPARISON_WORKSPACE" \
  --dataset "$DATASET" \
  --dataset "$OTHER_DATASET" \
  --category sec_filings --form 10-K --form 10-Q --form S-1 --form S-1/A \
  --since "$SINCE" --until "$UNTIL"
```

筛选说明：

- `--category`、`--form` 可重复；表单类型按归档的 `form` 字段精确匹配。
- `--since`、`--until` 按**发布日期**筛选，含起止日，不代替财务报告期。
- 启用日期筛选时，未知日期、只有季度或年份的记录默认排除；`--include-undated` 可保留，但不会把它们称为期间内发布。
- 未指定日期范围时保留无日期补充材料。`--limit-records N` 按清单顺序限制整个导入任务的归档记录总数，适合试跑。
- 普通目录也可导入；没有归档元数据时，用 `--ticker` 明确公司，发布日期标为未知。混合公司目录应拆开准备元数据，而不是给全部文件猜测发行人。
- 时间/公司范围在**建立图谱前**确定；不能对全量社区摘要做事后过滤并声称严格时间隔离。需要另一个范围时，新建资料库。

## 如何选择问答方式

| 方式 | 适用问题 | 证据 |
|---|---|---|
| `local`（默认） | 客户、供应商、产品、组织、业务风险及其关系 | 实体、关系、相关原文和社区摘要 |
| `global` | 跨文档主题、业务变化、整体风险 | 社区报告，经 map-reduce 汇总 |
| `basic` | 具体金额、日期、披露内容；与图谱模式比较 | 原文向量检索 |
| `drift` | 需要进一步探索的复杂问题 | 社区信息与多步局部检索 |

模式由用户显式选择，不通过脆弱的关键词规则自动猜测。中文问题可以检索英文资料，回答要求沿用问题语言。

回答 JSON 含 `answer`、`context`、`evidence`、`scope`。`[Data: Sources (7)]` 对应证据中的 `Sources:7`；实体、关系及社区编号也可追溯。原文证据保留网页 URL、本地文件、PDF 页码/幻灯片/工作表和文字片段。证据列表是检索结果，**不等于每项都被模型引用**；`graph_background` 表示图谱背景，不能当作某个数字的直接证明。

提示词要求：不凭模型常识补足证据；区分发行人和交易对手，区分实际数与指引，保留币种、单位、财务期间与 GAAP 口径，说明矛盾与信息不足。财务计算仍由模型生成，不是确定性会计计算引擎；关键数值需要对照原文。

## 导入行为与边界

- 支持 PDF、HTML/HTM、TXT/Markdown、DOCX、PPTX、XLSX/XLS、CSV 和 XML。JSON 归档清单用作元数据，不把审计 JSON/脚本当正文。
- 优先使用归档 `index.json` 和每条 `meta.json`；SEC 优先原始正文及 exhibits，跳过索引页、重复格式、XBRL 辅助表；普通网页选一个正文版本，并读取附件。
- HTML 表格保留行列分隔；PDF 按页提取排版文本，失败时回退到普通文本提取；Office 按正文/幻灯片/工作表定位。
- 按内容去重并记录别名，保留不同公司和发布日期下的相同文本。二进制内容相同的附件在单次导入中复用提取结果。
- 不提供 OCR、图像图表理解或自动下载音视频。部分扫描 PDF 无文字，图形标签、复杂跨页表格和排版可能缺失。`ingestion-report.json` 记录空页、提取失败、PDF 回退、无正文记录以及视频仅有链接等情况；它不是文档完整性保证。
- Excel 使用缓存单元格值，不计算公式。DOCX/PPTX 不解析嵌入图表和备注。旧版 `.doc`/`.ppt`、压缩包和图片未处理，需先转换。
- 目前为本机单用户工具；没有公网认证、多租户或后台任务队列。界面默认只监听 `127.0.0.1`。每个资料库同时只运行一个索引/查询操作。
- `prepare` 不覆盖已有语料。索引失败不会产生可用标记；修正后重跑 `index` 可复用上游缓存。更改语料、配置或提示词后，问答会拒绝使用过期索引。

## 网关适配

实测该 OpenClaw 网关会改变模型响应中的 `<|>` 等尖括号标记；GraphRAG 原生提取器因此无法解析实体。`ir_graphrag/openclaw.py` 通过官方 completion factory 注册 `ir_openclaw`：仅将标记过的图谱抽取请求改用 `|||` 与 `END_OF_GRAPH`，返回后恢复上游格式。其他请求（包括流式问答）原样传递，GraphRAG 的图提取、聚类、社区报告及查询代码保持上游版本。

更换其他兼容 API 时，`init --api-base ... --model ... --embedding-model ... --vector-size ...`，并在新资料库 `.env` 设置该服务的密钥。此时默认使用普通 LiteLLM provider。不要直接运行上游 CLI 来加载 `ir_openclaw` 类型：请使用本项目 `ir-graphrag` 命令完成注册。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ir-graphrag doctor --root "$WORKSPACE"
.venv/bin/ir-graphrag status --root "$WORKSPACE"
```

测试覆盖：日期范围、未知日期、公司隔离、去重别名、路径越界、SEC 正文选择、表格数字、空语料失败、配置与密钥处理、索引过期检测、引用映射及网关格式适配。导入、索引及问答的检查方法见 `VALIDATION.md`。

上游参考：[查询方式](https://microsoft.github.io/graphrag/query/overview/)、[初始化与索引](https://microsoft.github.io/graphrag/get_started/)。实现以固定版本源码为准；上游在线文档的部分配置示例可能来自其他版本。
