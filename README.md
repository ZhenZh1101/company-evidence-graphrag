# Company Evidence GraphRAG

A local financial document research system built on [Microsoft GraphRAG 3.2.0](https://github.com/microsoft/graphrag/tree/v3.2.0). It provides source document retrieval, traceable answers, calculations using cited source values, and analysis of entity relationships and community themes.

Financial figures are grounded in source documents first: **quarterly and year-to-date figures, GAAP and Core/adjusted measures, actual results and guidance, and publication dates and financial periods are treated separately**. Source document mode uses SQLite FTS5 for retrieval and upstream GraphRAG `BasicSearch` to generate answers; graph mode uses the upstream indexing and local/global/DRIFT APIs. Source document mode does not require an expensive full graph build and does not claim to perform graph reasoning.

## Hugging Face Docker Space

The application can be deployed to a Hugging Face Space using Docker. The image uses Python 3.12, and the application listens on port 7860. A private Space also requires platform access; application login is configured separately.

Before starting, set `GRAPHRAG_AUTH_USERS_JSON` under the Space's **Settings → Secrets** to a JSON mapping from usernames to PBKDF2-SHA256 password hashes. The application reads this configuration from its process environment and has no built-in accounts. Model keys are also injected through Secrets: use `OPENAI_API_KEY` for OpenAI, `DEEPSEEK_API_KEY` for DeepSeek, and `ZAI_API_KEY` for Z.ai. When Chat and Embedding use different services, configure the corresponding keys separately.

The packaging script reads libraries under the project's `workspaces/` directory and migrates API configuration and absolute paths for deployment copies without changing the original libraries. It excludes `.env`, logs, caches, backups, and symbolic links. The Docker build context contains only the files required by the application and the generated library archive, excluding tests and local data directories. Completed graphs have their input fingerprints, tables, and vectors validated during packaging; incomplete graphs include only source documents, without partial graph outputs. The model used to extract the graph is retained in the migration record. Changing the query model does not re-extract the graph.

```bash
.venv/bin/python scripts/package_space_workspaces.py \
  --output deployment/workspaces.tar.gz --provider openai
docker build -t company-evidence-graphrag .
```

`--provider` accepts `openai`, `deepseek`, `zai`, or `openclaw`; `--model` overrides the Chat model. OpenClaw also requires `--api-base` to specify a public HTTPS endpoint without embedded credentials, plus `GRAPHRAG_API_KEY`. A local loopback address is not accessible from a remote Space. Packaging supports only local LanceDB and 3072-dimensional vectors compatible with `text-embedding-3-large`. OpenAI, DeepSeek, and Z.ai deployment copies use OpenAI Embedding and therefore require `OPENAI_API_KEY`.

To include an additional verified graph backup, add `--snapshot` to the same packaging command, followed by a unique snapshot directory name, the backup path, and the original library path:

```bash
.venv/bin/python scripts/package_space_workspaces.py \
  --output deployment/workspaces.tar.gz --provider openai \
  --snapshot "$SNAPSHOT_NAME" "$SNAPSHOT_SOURCE" "$ORIGINAL_WORKSPACE"
```

Snapshot backups must contain a valid `index-ready.json`. `--snapshot` can be repeated. The library archive is stored in the ignored `deployment/` directory and should not be committed to Git.

Libraries in the image are fixed snapshots taken at packaging time; subsequent local changes are not synchronized automatically. Persistent storage has not been configured for answers and data imported at runtime, so they may be lost when the Space is rebuilt or restarted. Download any answers you want to retain as JSON.

## Account Service Configuration

The account service reads a JSON mapping from usernames to PBKDF2-SHA256 password hashes from the application process environment variable `GRAPHRAG_AUTH_USERS_JSON`. It has no built-in accounts. A library's model configuration `.env` is not used to configure interface accounts. Generate a password hash, then export the JSON configuration in the shell that starts the application:

```bash
export GRAPHRAG_AUTH_USERS_JSON="$(.venv/bin/python -c 'import getpass, json; from ir_graphrag.auth import hash_password; print(json.dumps({"reader": hash_password(getpass.getpass("New login password: "))}))')"
```

Replace `reader` with your username. The password is entered interactively and is not written to shell history. To configure multiple accounts, include their usernames and hashes in the same JSON object. Do not put production account configuration or passwords in source code or tests.

The web interface requires login before reading libraries or executing commands and remains locked when account configuration is missing or invalid. Account configuration is loaded and cached on first use; restart the application after changing accounts or passwords.

All logged-in users share libraries and saved answers; data is not isolated between accounts. Sessions are stored on the server and last at most 12 hours. Logging out revokes the session and clears the current research state; refreshing the page or opening a new tab requires login again. Sessions, login rate limits, and password calculation concurrency limits are all held in a single process, and restarting the application invalidates sessions. These states must be shared separately before adding workers or replicas. Remote access should use HTTPS.

JSON exports use inline downloads on the current page, avoiding Streamlit media file URLs that could be accessed without login.

## Getting Started

After installing dependencies, start the interface from the project root:

```bash
.venv/bin/streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

Open <http://127.0.0.1:8501>, log in, and select an existing library, or specify your local data directory in the sidebar to create a new one. The interface supports company and cutoff-date filters, explicit fiscal-year/fiscal-quarter filters, inspection of cited evidence, and Decimal calculations using source document operands.

The interface uses a light gray and dark green research workspace layout. The sidebar groups library, company, and date-range selection, while the main area supports questions, answers, and two-column evidence cards. The sidebar can be collapsed on narrow screens. Graph building, ingestion quality, and data import remain in the sidebar; numerical calculations appear below the answer.

The interface and new answers default to **English**. The **Language** selector in the upper-right corner switches between English and Simplified Chinese. The selected language is stored in the URL as `?lang=en` or `?lang=zh` and persists after refresh. Switching languages preserves the current question, filters, and calculation inputs. Original documents, quoted excerpts, and saved answers retain their original language. CLI `ask` answers in English by default; use `--language zh` for Chinese or `--language en` to specify English explicitly.

Create a new library when you need the updated ingestion behavior to avoid overwriting an existing index. Each library's `ingestion-report.json` records the actual scope and extraction issues. Imported text does not mean graph indexing is complete.

## Models and Installation

The default connection uses the user-specified OpenClaw service:

- Chat: `http://127.0.0.1:18789/v1/chat/completions`
- Embedding: `http://127.0.0.1:18789/v1/embeddings`
- Chat model and the `model` field in Embedding request bodies: `openclaw/llm-gpt55`
- Actual Embedding model: `openai/text-embedding-3-large`, specified through the `x-openclaw-model` request header. A live probe returned **3072 dimensions**.
- At runtime, the application reads `gateway.auth.token` from `~/.openclaw/openclaw.json`. Alternatively, set `GRAPHRAG_API_KEY` or `OPENCLAW_CONFIG` in the library's `.env`. Do not commit real keys.

Python 3.11–3.13 and Streamlit 1.65+ are required. Language switching relies on stable widget identities to preserve inputs. The validated environment uses Python 3.12.14, and the locked dependencies include Streamlit 1.65.0.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.lock
```

To install only direct dependencies and allow transitive versions to be resolved again, use `.venv/bin/python -m pip install -e .`.

### OpenAI, Z.ai, and DeepSeek

Select a provider through CLI `init --provider` or the interface's new-library import section. OpenClaw remains the default, and existing libraries are not modified automatically.

| `--provider` | Default Chat model | API base | Key environment variable |
|---|---|---|---|
| `openclaw` | `openclaw/llm-gpt55` | `http://127.0.0.1:18789/v1` | `GRAPHRAG_API_KEY` or local gateway configuration |
| `openai` | `gpt-4.1-mini` | `https://api.openai.com/v1` | `OPENAI_API_KEY` |
| `zai` | `glm-4.7` | `https://api.z.ai/api/paas/v4` | `ZAI_API_KEY` |
| `deepseek` | `deepseek-flash` | `https://api.deepseek.com` | `DEEPSEEK_API_KEY` |

Use `--model` to override model names. Presets do not promise the latest models; actual access depends on your account. The integration reuses existing GraphRAG/LiteLLM dependencies. API references: [OpenAI Chat Completions](https://developers.openai.com/api/reference/python/resources/chat/subresources/completions/methods/create), [Z.ai JSON output](https://docs.z.ai/guides/capabilities/struct-output), and [DeepSeek API](https://api-docs.deepseek.com/). Z.ai/DeepSeek Chat endpoints use JSON Object mode. The adapter adds the schema required by GraphRAG to the prompt and validates responses against the original schema.

```bash
# OpenAI: Chat and Embedding share OPENAI_API_KEY
.venv/bin/ir-graphrag init --root workspaces/openai-demo --provider openai

# Z.ai or DeepSeek: Chat uses the provider's own key; graph building uses OpenAI Embedding by default
.venv/bin/ir-graphrag init --root workspaces/zai-demo --provider zai
.venv/bin/ir-graphrag init --root workspaces/deepseek-demo --provider deepseek

# DeepSeek Chat + existing local OpenClaw Embedding
.venv/bin/ir-graphrag init --root workspaces/deepseek-local \
  --provider deepseek --embedding-provider openclaw
```

After initialization, add the required keys to **the corresponding library's `.env`** (see the repository's `.env.example` for the format), or set them in the process environment that starts CLI/Streamlit. Process environment values take precedence. Initialization neither reads nor copies real keys, and existing `.env` contents are preserved. Keys are not shared automatically between providers.

**Financial source document Q&A requires only a Chat key**, without an OpenAI or other Embedding key. Check it with `doctor --chat-only`. Graph building and vector retrieval require an Embedding service. This project uses Z.ai/DeepSeek as Chat providers, paired by default with OpenAI's `text-embedding-3-large` (3072 dimensions); OpenClaw is also available. See the [OpenAI Embedding documentation](https://developers.openai.com/api/docs/guides/embeddings) and [Z.ai API directory](https://docs.z.ai/llms.txt).

For OpenClaw, `--embedding-model` and the interface's model input specify the routing target (default: `openai/text-embedding-3-large`). The application places it in the `x-openclaw-model` request header while retaining `openclaw/llm-gpt55` in the request body. For direct OpenAI access, use `text-embedding-3-large`. If `--vector-size` is omitted, the preset value of 3072 dimensions is used.

When migrating an existing library from an older OpenClaw configuration, update `embedding_models.default_embedding_model.call_args.extra_headers.x-openclaw-model` and `vector_store.vector_size`, and give `embed_text.model_instance_name` a new cache name (for example, `text_embedding_3_large`) to avoid reusing vectors from the old model. Then rebuild the graph. Source document Q&A is unaffected, and Chat extraction caches can be retained. New libraries automatically distinguish vector caches by Embedding endpoint, model, and dimensions. `doctor` displays the actual routing target.

```bash
.venv/bin/ir-graphrag doctor --root workspaces/deepseek-demo --chat-only
# A full check calls Chat and Embedding, checks JSON and vector dimensions, and incurs corresponding API usage
.venv/bin/ir-graphrag doctor --root workspaces/deepseek-demo
```

Override endpoints, models, and key variables independently:

```bash
.venv/bin/ir-graphrag init --root workspaces/custom-api \
  --provider openai --model my-chat-model --api-base https://chat.example.com/v1 \
  --api-key-env CHAT_API_KEY \
  --embedding-provider openai --embedding-model my-embedding-model \
  --embedding-api-base https://embedding.example.com/v1 \
  --embedding-api-key-env EMBEDDING_API_KEY --vector-size 1536
```

`--api-base` / `--embedding-api-base` take an API base URL without `/chat/completions` or `/embeddings`. `--vector-size` sets the expected dimensions in the vector store; it does not automatically reduce the model's output dimensions and must match the selected model's actual output. For existing libraries, edit `completion_models` / `embedding_models` in `settings.yaml`: reference keys as `${VARIABLE_NAME}`; for Z.ai/DeepSeek completions, set `type` to `ir_json_chat` and `model_provider` to `openai`. Run `doctor` after configuration changes. Rebuild existing graphs before querying them; financial source document Q&A does not require a graph rebuild.

## Financial Source Document Q&A

The following commands use caller-defined environment variables: `WORKSPACE` is an existing library path, `TICKER` is the company ticker, `PERIOD` is the financial period in the question, and `AS_OF` is the publication-date cutoff (`YYYY-MM-DD`). Set `FISCAL_YEAR` and `FISCAL_QUARTER` when strict fiscal-year/fiscal-quarter filters are required:

```bash
.venv/bin/ir-graphrag ask --root "$WORKSPACE" \
  "What were ${TICKER}'s sales for ${PERIOD} and the comparable prior-year period? Distinguish quarterly from year-to-date figures and cite the sources." \
  --ticker "$TICKER" --as-of "$AS_OF" --fiscal-year "$FISCAL_YEAR" --fiscal-quarter "$FISCAL_QUARTER" \
  --output "$WORKSPACE/answers/answer.json"
```

The CLI defaults to `--method financial`. The first query automatically builds a local full-text index; **building this index does not call a model**. Answer generation calls Chat. To retrieve evidence without calling a model:

```bash
.venv/bin/ir-graphrag search --root "$WORKSPACE" \
  "${TICKER} ${PERIOD} GAAP and adjusted revenue" --ticker "$TICKER" --as-of "$AS_OF" \
  --output "$WORKSPACE/answers/evidence.json"
```

Source document retrieval uses explicit English/Chinese financial term expansion, BM25, and metric phrase searches, combining results through reciprocal-rank fusion and prioritizing tables with exact amounts. Questions covering several metrics retain candidates for each metric. Long passages include nearby printed table headers to help preserve units and periods. This is not a full semantic retriever. If an uncommon synonym produces no matches, try another metric name; use a library with a completed graph for relationship questions.

**Filter boundaries:**

- Query `--ticker` can be repeated. `--as-of` is a **publication-date cutoff**; documents with unknown publication dates are excluded. Dates are normalized before comparison, and documents published after the cutoff do not enter the model context.
- `--fiscal-year` / `--fiscal-quarter` match only fields explicitly supplied by the archive and **do not infer fiscal quarters from publication dates**. Many SEC/news records lack these fields. When uncertain, leave these filters disabled, state the financial period in the question, and have the answer cite the original table headers.
- `--form` can be repeated and matches the archive's form field.
- No matches means only that no evidence matched the current filters and keywords, not that the company did not publish documents for that period.
- Graph local/global/basic/DRIFT modes do not accept these query-time filters, preventing full-scope community summaries from being presented as strictly time-isolated results. Graph query scope must be established during ingestion.

## Importing a New Financial Library

Set `WORKSPACE` to the new library path, `DATASET` to the local data directory, and `TICKER` to the company ticker:

```bash
.venv/bin/ir-graphrag init --root "$WORKSPACE"
.venv/bin/ir-graphrag prepare --root "$WORKSPACE" --profile financial \
  --dataset "$DATASET" --ticker "$TICKER"
.venv/bin/ir-graphrag doctor --root "$WORKSPACE"
```

When combining archives from different companies, repeat `--dataset` and let company tickers be read from each archive's metadata. Do not assign one `--ticker` to mixed-company data.

The `financial` scope selects periodic/relevant financial filings, earnings materials, calls, annual reports, investor conferences, and financial press releases, excluding ownership forms, general product blogs, and papers. `prepare` still defaults to `--profile all` for compatibility with older commands; the interface defaults to financial.

To narrow the scope further:

- Use `--category quarterly_results --category annual_report`, or `--category sec_filings --form 10-K --form 10-Q`. These are intersected with the financial scope.
- `--since "$SINCE" --until "$UNTIL"` filters by caller-defined dates (`YYYY-MM-DD`), including both endpoints. Unknown dates are excluded by default; use `--include-undated` to retain them. Without a date range, undated supplementary records are retained.
- `--limit-records N` limits the total number of records for the entire task in archive-manifest order and can be used for trial runs.
- Ordinary document directories can also be imported; use `--ticker` to specify the company. Publication dates and fiscal quarters are not guessed without archive metadata. For ordinary files, the financial scope relies on filenames; use `--profile all` when names do not follow expected conventions.

Updated ingestion behavior preserves `fiscal_year`, `fiscal_quarter`, `report_year`, and `report_date`; locates HTML tables separately as `table N`, retaining spanning headers, row labels, currencies, and accounting negatives; and prefers a complete TXT file in the same directory when HTML contains `[Truncated]`, recording the fallback.

## Calculations Using Source Values

Automatic arithmetic in answers is still model output. For programmatic calculations, select two explicitly cited values from a saved answer or `search` result and use the interface calculator or CLI. Set `ANSWER_JSON` to your answer/search-result path and `OPERANDS_JSON` to an external operands JSON file:

```bash
.venv/bin/ir-graphrag calculate \
  --answer "$ANSWER_JSON" \
  --operands "$OPERANDS_JSON" \
  --output "$WORKSPACE/answers/comparison.json"
```

The external operands JSON contains `current` and `previous` objects, each requiring the following fields. This example illustrates the structure only; replace the placeholders with a real source ID from the current answer, the full source quote, and the corresponding value labels:

```json
{
  "source_id": "Source ID from the answer",
  "quote": "Complete source line containing the value",
  "value": "Complete signed number from the source",
  "unit": "USD millions",
  "period": "Financial period corresponding to the source",
  "basis": "GAAP",
  "metric": "Metric corresponding to the source"
}
```

The calculator validates that the citation exists and that the value is a complete signed token in the source. It does not allow selecting only some digits or stripping parentheses or a minus sign to turn a negative value positive. Decimal normalizes USD amounts in units, thousands, millions, and billions; per-share amounts and percentages cannot be mixed with monetary amounts. Both periods must use matching metric and accounting-basis labels, and the periods must differ.

The result includes the difference and rate of change; subtracting percentages also returns percentage-point and basis-point differences. When the prior-period value is zero or negative, potentially misleading growth rates are omitted.

**The user explicitly specifies units, metrics, periods, and GAAP basis. The program validates numbers and arithmetic; it does not automatically prove these semantic labels are correct.** The calculator currently supports only USD, USD per share, and percent.

## Graph Analysis

```bash
.venv/bin/ir-graphrag index --root "$WORKSPACE"
.venv/bin/ir-graphrag ask --root "$WORKSPACE" \
  'What relationships connect the main customers, products, and business risks?' --method local
```

| Method | Suitable questions | Prerequisites |
|---|---|---|
| `financial` (CLI/interface default) | Exact disclosures, amounts, periods, and accounting bases | Imported text |
| `local` | Companies, customers, products, risks, and relationships | Completed graph |
| `global` | Cross-document themes and overall trends | Completed graph; community report map-reduce |
| `basic` | Upstream vector-based source document retrieval for comparison with financial retrieval | Completed index and vectors |
| `drift` | Multi-step exploration of complex questions | Completed graph; may require many model calls |

A full graph build can take a long time; start with the financial scope or a small sample. Token counts in the ingestion report are simply character counts divided by 4, not billing estimates. An upstream custom-model cost recorded as zero does not mean the model is free.

### Resuming Incomplete Embedding Generation

This applies to GraphRAG 3.2 workspaces where graph tables, community reports, and entity vectors are complete, but report or source-text vectors remain unfinished. The workspace must not already have an `index-ready.json` completion marker, and its inputs must match the ingestion report and saved documents.

```bash
.venv/bin/python scripts/resume_embeddings.py --root "$WORKSPACE"
```

The script first backs up the configuration, output directory, and default log path `logs/indexing-engine.log`. It sets `embed_text.batch_max_tokens` to 1,200 and checks the actual corpus to ensure individual inputs and batches do not exceed the local gateway's 8,192-character limit; 1,200 tokens alone does not guarantee compliance with the character limit. It resumes only report and source-text vectors, writing the completion marker only after validating vectors and preserved files. The backup fully copies only the output directory specified in the configuration. Use the default directory layout; custom vector stores outside the output directory are not included in this backup.

## Citations, Quality, and Operational Limits

- Answer JSON retains `answer`, `evidence`, `context`, scope, and `citation_audit`. `Sources:123` corresponds to `[Data: Sources (123)]`. The interface can display only evidence actually cited and show original paths, PDF page numbers/table locations, web URLs, and excerpts.
- Citation auditing checks only whether IDs exist; it does not determine whether conclusions are correct. Financial mode rejects model answers containing unknown citation IDs, and missing citations are shown separately. Graph context in `graph_background` cannot serve as direct evidence for specific amounts.
- The financial local retrieval index rebuilds automatically when the corpus or metadata changes. Graph modes reject stale indexes after changes to the corpus, configuration, or prompts. After making corrections, rerun `index` to reuse upstream caches.
- Supported formats are PDF, HTML/HTM, TXT/Markdown, DOCX, PPTX, XLSX/XLS, CSV, and XML. Main documents and attachments are selected using archive manifests, excluding audit files, SEC index pages, and duplicate formats. Deduplicated text retains distinctions and aliases for companies, dates, and periods.
- There is no OCR, visual chart understanding, audio/video downloading, or automatic transcription. Complex tables, chart labels, and scanned pages may remain incomplete. Excel files are read using cached values, without formula calculation; DOCX/PPTX parsing does not include embedded charts or notes. Legacy `.doc`/`.ppt` files must be converted first.
- This is a local tool that listens only on `127.0.0.1` by default. The interface requires login, without data isolation between accounts or a task queue. Login protects the web interface; CLI and file access still follow local permissions. Each library runs only one indexing/query operation at a time.
- Observed OpenClaw behavior changes the `<|>` marker. The `ir_openclaw` provider, registered through the upstream completion factory, uses ASCII transport escaping only for graph extraction, then restores the native format. Use this project's CLI to complete provider registration.

## Validation and Development

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ir-graphrag status --root "$WORKSPACE"
```

Evaluation cases are supplied by the caller; no companies, financial values, cutoff dates, or answer paths are built in. Set `CASES` to an external cases JSON file. Its contents must be a nonempty array, with each item containing:

- `name`: a unique answer filename without an extension, using only letters, digits, `_`, and `-`.
- `question`: a nonempty question string.
- `filters`: an object describing the query scope, optionally containing `tickers` / `forms` string arrays, `as_of` (`YYYY-MM-DD`), `fiscal_year`, and `fiscal_quarter`. Specify only scope supported by the source material.
- `expected`: an array of expected values as decimal strings.
- `units`: an array of unit labels to check, supporting `million`, `thousand`, and `per_share`.

When both `expected` and `units` are empty, the evaluation checks whether the answer explicitly reports insufficient evidence within that scope. Expected values should be entered by a person checking the original disclosures. Offline mode reads the library's `answers/<name>.json`; live mode calls the model and saves answers.

```bash
# Recheck existing answers offline without calling a model:
.venv/bin/python scripts/evaluate_financial.py --root "$WORKSPACE" --cases "$CASES"
# Generate and check new answers using the current retriever and model:
.venv/bin/python scripts/evaluate_financial.py --root "$WORKSPACE" --cases "$CASES" \
  --live --output "$WORKSPACE/evaluation.json"
```

Evaluation checks configured values and minus signs, unit labels, query scope, citation IDs, values in cited direct evidence, and the existence of local source files. **This is a functional check of specified cases, not an assessment of semantic financial accuracy across the entire corpus.** A value appearing in evidence does not automatically prove that the model selected the correct row, column, or accounting basis.

See `VALIDATION.md` for methods to check ingestion, Q&A, citations, and calculations.
