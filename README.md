# 上市公司 GraphRAG

基于 [Microsoft GraphRAG 3.2.0](https://github.com/microsoft/graphrag/tree/v3.2.0) 的本地金融文档研究系统。提供财务原文检索、可追溯回答、原文数值核算，以及实体关系、社区主题分析。

财务数字优先从原文取证：**季度与年初至今、GAAP 与 Core/调整后、实际与指引、公告日与财务期间分别处理**。原文模式使用 SQLite FTS5 检索，交由上游 GraphRAG `BasicSearch` 生成回答；图谱模式调用上游建图、local/global/DRIFT API。原文模式不要求先完成昂贵的全量图谱，也不声称自己使用了图谱推理。

## 直接使用

安装依赖后，在项目根目录启动界面：

```bash
.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

打开 <http://127.0.0.1:8501>，选择已有资料库，或在侧栏指定自己的本地数据目录建立新资料库。界面支持公司与截止日期筛选、明确财年/财季筛选、实际引用证据查看，以及带原文操作数的 Decimal 核算。

需要新版导入行为时新建资料库，避免覆盖已有索引。资料库 `ingestion-report.json` 记录实际范围与提取问题；已导入文本不代表已完成图谱索引。

## 模型与安装

默认接入用户指定的 OpenClaw：

- Chat：`http://127.0.0.1:18789/v1/chat/completions`
- Embedding：`http://127.0.0.1:18789/v1/embeddings`
- 两者模型名：`openclaw/llm-gpt55`
- Embedding：真实探测为 **1536 维**。
- 运行时读取 `~/.openclaw/openclaw.json` 的 `gateway.auth.token`，也可在资料库 `.env` 设置 `GRAPHRAG_API_KEY` 或 `OPENCLAW_CONFIG`；不提交真实密钥。

需要 Python 3.11–3.13。当前验证环境为 Python 3.12.14。

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

只安装直接依赖并允许重新解析传递版本，可用 `.venv/bin/python -m pip install -e .`。更换兼容 API：`init --api-base ... --model ... --embedding-model ... --vector-size ...`，在新资料库 `.env` 设置该服务的密钥，再运行 `doctor`。

## 财务原文问答

以下命令使用调用者设置的环境变量：`WORKSPACE` 为已有资料库路径，`TICKER` 为公司代码，`PERIOD` 为问题中的财务期间，`AS_OF` 为发布日期截止日（`YYYY-MM-DD`）。需要严格财年/财季筛选时，设置 `FISCAL_YEAR` 与 `FISCAL_QUARTER`：

```bash
.venv/bin/ir-graphrag ask --root "$WORKSPACE" \
  "${TICKER} ${PERIOD} 销售额和上年同期各是多少？请区分季度与累计数并注明来源。" \
  --ticker "$TICKER" --as-of "$AS_OF" --fiscal-year "$FISCAL_YEAR" --fiscal-quarter "$FISCAL_QUARTER" \
  --output "$WORKSPACE/answers/answer.json"
```

CLI 默认 `--method financial`。首次查询自动建立本地全文索引，**建立此索引不调用模型**；生成回答会调用 Chat。只查证据、不调用模型：

```bash
.venv/bin/ir-graphrag search --root "$WORKSPACE" \
  "${TICKER} ${PERIOD} GAAP 与调整后收入" --ticker "$TICKER" --as-of "$AS_OF" \
  --output "$WORKSPACE/answers/evidence.json"
```

原文检索采用显式中英财务词扩展、BM25 和指标短语检索，以 reciprocal-rank fusion 合并结果，优先精确金额表；同问多个指标时为各指标保留候选。长段落携带临近印刷表头，帮助保留金额单位与期间。该方法不是完整的语义检索器；罕见同义词未命中时可以改写指标名称，关系问题使用已建图资料库。

**筛选边界：**

- 查询 `--ticker` 可重复；`--as-of` 是资料**发布日期截止日**，未知发布日期会排除。日期输入被标准化后比较，截止日之后的资料不进入模型上下文。
- `--fiscal-year` / `--fiscal-quarter` 只匹配归档明确提供的字段，**不从公告日推断财季**。大量 SEC/新闻记录缺这两个字段；不确定时不要启用这两个过滤器，在问题里写明财务期间，让回答引用原文表头。
- `--form` 可重复，按归档表单字段匹配。
- 未命中只表示当前筛选与关键词下没有匹配证据，不等于公司没有发布该期间的资料。
- 图谱 local/global/basic/DRIFT 不接受上述查询时过滤，防止用全量社区摘要冒充严格时间隔离。图谱查询需要在导入时确定范围。

## 导入新的金融资料库

将 `WORKSPACE` 设为新资料库路径、`DATASET` 设为本地数据目录、`TICKER` 设为公司代码：

```bash
.venv/bin/ir-graphrag init --root "$WORKSPACE"
.venv/bin/ir-graphrag prepare --root "$WORKSPACE" --profile financial \
  --dataset "$DATASET" --ticker "$TICKER"
.venv/bin/ir-graphrag doctor --root "$WORKSPACE"
```

合并不同公司归档时可重复 `--dataset`，让公司代码从各归档元数据读取，不对混合资料统一指定 `--ticker`。

`financial` 范围选择定期/相关财务申报、业绩材料、电话会、年报、投资者会议和财务发布稿，排除所有权表单、普通产品博客和论文。`prepare` 默认仍为 `--profile all` 以兼容旧命令；界面默认选择 financial。

进一步收窄：

- `--category quarterly_results --category annual_report`，或 `--category sec_filings --form 10-K --form 10-Q`；与 financial 范围取交集。
- `--since "$SINCE" --until "$UNTIL"` 按调用者设置的日期（`YYYY-MM-DD`）筛选，含起止日；未知日期默认排除，`--include-undated` 可保留。不设日期范围时保留无日期补充记录。
- `--limit-records N` 按归档清单顺序限制整个任务的记录总数，可用于试跑。
- 普通文档目录也可导入，使用 `--ticker` 明确公司。没有归档元数据时不猜测发布日期或财季；financial 范围对普通文件依赖文件名，名称不规范时用 `--profile all`。

新增导入行为：保留 `fiscal_year`、`fiscal_quarter`、`report_year` 和 `report_date`；HTML 表格独立定位为 `table N`，保留跨度表头、行标签、币种及会计负数；发现 `[Truncated]` 的 HTML 时优先使用同目录完整 TXT，并记录回退。

## 原文数值核算

问答中的自动算术仍是模型输出。需要程序核算时，从已保存回答或 `search` 结果选择两个明确引用的数值，使用界面核算器或 CLI。将 `ANSWER_JSON` 设为自己的答案/检索结果路径、`OPERANDS_JSON` 设为外部操作数 JSON 路径：

```bash
.venv/bin/ir-graphrag calculate \
  --answer "$ANSWER_JSON" \
  --operands "$OPERANDS_JSON" \
  --output "$WORKSPACE/answers/comparison.json"
```

外部操作数 JSON 包含 `current` 与 `previous` 对象，两者都需要以下字段。下面仅说明结构；占位内容必须替换为当前答案中的真实来源编号、完整原文及相应数值标签：

```json
{
  "source_id": "答案中的来源编号",
  "quote": "包含数值的完整原文行",
  "value": "原文中的完整带符号数字",
  "unit": "USD millions",
  "period": "原文对应的财务期间",
  "basis": "GAAP",
  "metric": "原文对应的指标"
}
```

核算器验证引用实际存在、数值是原文中的完整带符号 token；不能截取数值中的部分数字，也不能裁掉括号或负号把负数变成正数。用 Decimal 归一化 USD、千/百万/十亿美元；每股金额和百分率不能与金额混算。两期指标与会计口径标签必须一致，期间必须不同。

返回差额、变化率；百分率相减另外返回百分点和基点。前期为零或负数时不给出易误导的增长率。

**单位、指标、期间和 GAAP 口径由使用者明确指定；程序校验数字与算术，不自动证明这些语义标签正确。** 当前核算器限定 USD、USD per share、percent。

## 图谱分析

```bash
.venv/bin/ir-graphrag index --root "$WORKSPACE"
.venv/bin/ir-graphrag ask --root "$WORKSPACE" \
  '主要客户、产品与业务风险之间有什么关系？' --method local
```

| 方法 | 适用问题 | 前提 |
|---|---|---|
| `financial`（CLI/界面默认） | 精确披露、金额、期间与会计口径 | 已导入文本 |
| `local` | 公司、客户、产品、风险及关系 | 完成图谱 |
| `global` | 跨文档主题与整体趋势 | 完成图谱，社区报告 map-reduce |
| `basic` | 上游向量原文检索，与金融检索比较 | 完成现有索引及向量 |
| `drift` | 复杂问题的多步探索 | 完成图谱，可能产生较多模型调用 |

全量建图可能运行很久；先选财务范围或小样本。导入报告的 token 数只是字符数除以 4，不是计费估算。上游自定义模型成本记录为零不代表免费。

## 引用、质量和运行边界

- 回答 JSON 保留 `answer`、`evidence`、`context`、范围以及 `citation_audit`。`Sources:123` 对应 `[Data: Sources (123)]`。界面可只显示实际引用的证据，并查看原始路径、PDF 页码/表格位置、网页 URL 和片段。
- 引用审计只核对编号是否存在，不是结论正确性判定。financial 模式拒绝接受含未知引用编号的模型回答；无引用会单独显示。图谱背景 `graph_background` 不能当作具体金额的直接证明。
- financial 的本地检索索引随语料或元数据变化自动重建。图谱模式在语料/配置/提示词变化后拒绝使用过期索引；修正后重跑 `index` 可复用上游缓存。
- 支持 PDF、HTML/HTM、TXT/Markdown、DOCX、PPTX、XLSX/XLS、CSV、XML。按归档清单选择正文及附件，排除审计文件、SEC 索引页和重复格式；重复文本保留公司、日期与期间的区别及别名。
- 无 OCR、图形图表理解、音视频下载或自动转录；复杂表格、图形标签、扫描页仍可能不完整。Excel 读取缓存值，不计算公式；DOCX/PPTX 不解析嵌入图表和备注。旧 `.doc`/`.ppt` 需先转换。
- 本机单用户工具，默认只监听 `127.0.0.1`；没有公网认证、多租户或任务队列。每个资料库同时只执行一个索引/查询。
- OpenClaw 实测会改变 `<|>` 标记；通过上游 completion factory 注册的 `ir_openclaw` 仅对图抽取使用 ASCII 传输转义，随后恢复原生格式。请用本项目 CLI 完成 provider 注册。

## 验证与开发

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ir-graphrag status --root "$WORKSPACE"
```

评估案例由调用者提供，不内置公司、财务数值、截止日或答案路径。将 `CASES` 设为外部案例 JSON 文件路径；文件内容为非空数组，每项包含：

- `name`：唯一答案文件名，不含扩展名，仅使用字母、数字、`_`、`-`。
- `question`：非空问题字符串。
- `filters`：查询范围对象；可包含 `tickers` / `forms` 字符串数组、`as_of`（`YYYY-MM-DD`）、`fiscal_year`、`fiscal_quarter`。只填写原始资料能够支持的范围。
- `expected`：预期数值的十进制字符串数组。
- `units`：要核对的单位标签数组，支持 `million`、`thousand`、`per_share`。

`expected` 与 `units` 都为空时，检查该范围下是否明确回答证据不足。预期值应由人对照原始披露填写。离线模式读取资料库 `answers/<name>.json`；实时模式调用模型并保存回答。

```bash
# 离线复核已有答案，不调用模型：
.venv/bin/python scripts/evaluate_financial.py --root "$WORKSPACE" --cases "$CASES"
# 用当前检索器和模型重新回答并检查：
.venv/bin/python scripts/evaluate_financial.py --root "$WORKSPACE" --cases "$CASES" \
  --live --output "$WORKSPACE/evaluation.json"
```

评估核对配置中的数值及负号、单位标签、查询范围、引用编号、被引用直接证据内的数值和本地源文件存在性。**这是指定案例的功能检查，不是整个语料库的语义财务准确率评测。** 数值出现在证据里也不能自动证明模型选对了行列或会计口径。

导入、问答、引用与核算的检查方法见 `VALIDATION.md`。
