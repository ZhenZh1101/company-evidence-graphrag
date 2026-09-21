# Financial Text Validation Method

First, install the dependencies from the project root and set `WORKSPACE` to the path of the workspace to validate. The steps below describe how to reproduce the checks. Users keep the specific data, run reports, and UI screenshots locally; this document does not claim that any particular company or workspace has passed validation.

## Automated Checks

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ir-graphrag doctor --root "$WORKSPACE"
.venv/bin/ir-graphrag status --root "$WORKSPACE"
```

## Financial Question-Answering Evaluation

The caller supplies evaluation cases. No companies, financial values, cutoff dates, or answer paths are built in. Set `CASES` to the path of an external JSON case file. The file must contain a nonempty array, with each item containing:

- `name`: a unique answer filename without an extension, using only letters, digits, `_`, and `-`.
- `question`: a nonempty question string.
- `filters`: an object defining the query scope. It may include `tickers` / `forms` string arrays, `as_of` (`YYYY-MM-DD`), `fiscal_year`, and `fiscal_quarter`. Include only scope constraints supported by the source material.
- `expected`: an array of decimal strings representing the expected values.
- `units`: an array of unit labels to check. Supported labels are `million`, `thousand`, and `per_share`.

When both `expected` and `units` are empty, the evaluation checks whether the answer explicitly states that there is insufficient evidence within the specified scope. A person should fill in expected values by checking the original disclosures. Offline mode reads `answers/<name>.json` in the workspace; live mode calls the model and saves the answers.

```bash
# Recheck saved answers offline, without calling the model:
.venv/bin/python scripts/evaluate_financial.py --root "$WORKSPACE" --cases "$CASES"
# Generate and check new answers with the current retriever and model:
.venv/bin/python scripts/evaluate_financial.py --root "$WORKSPACE" --cases "$CASES" \
  --live --output "$WORKSPACE/evaluation.json"
```

The evaluation checks the configured values and negative signs, unit labels, query scope, citation identifiers, values in the cited direct evidence, and the existence of local source files. **This is a functional check of specified cases, not an evaluation of semantic financial accuracy across the entire corpus.** A value appearing in the evidence does not automatically prove that the model selected the correct row, column, or accounting basis.

## Ingestion Scope Checks

Inspect the workspace's `ingestion-report.json` for financial disclosure selection, body/table/page chunks, deduplication, file-level failures, blank/sparse content, associated media, and fallback from truncated HTML. Record the actual scope and extraction issues, and retain the source data. Completed ingestion does not prove that OCR, charts, or video content have been fully understood.

Archives may lack `fiscal_year/fiscal_quarter` fields. The system preserves the unknown state and does not infer the fiscal quarter from the announcement date. When strict fiscal year/quarter filtering is enabled, source text missing these fields is excluded. For press releases with missing metadata, query using the company, cutoff date, and the financial period stated in the question.

## Deterministic Calculations

Select two complete source lines from saved answers or retrieval results as the sources for the operands. Run `calculate` with an external operand JSON file and check each of the following:

- Handling of complete signed values, parentheses, and Unicode minus signs.
- USD conversions between thousands, millions, and billions, as well as percentage differences, percentage points, and basis points.
- Rejection of truncated numbers or negative signs, units with different dimensions, different metrics/accounting bases, and identical periods.
- No growth rate is produced for a zero or negative base-period value.

Value and citation validation and Decimal arithmetic are performed in code. Users specify the operands' period, unit, metric, and accounting-basis labels; the semantic interpretation is not automatically verified.

## Regression Checkpoints

1. Fall back from truncated HTML to a complete TXT file when available, and record the issue.
2. Preserve rowspan/colspan, accounting symbols, units, and independent table locations; carry nearby printed table headers into long TXT chunks.
3. Retain candidates for each metric when a question asks about multiple metrics, and check that the source tables containing the amounts are included in the evidence.
4. Keep publication date, reporting period end date, fiscal year, and fiscal quarter separate; do not infer them from the announcement date.
5. Normalize ISO dates during ingestion and querying; exclude material after the cutoff date from the model context.
6. Check citation identifiers in answers against the evidence actually cited; correct identifiers do not imply correct conclusions.
7. Check financial source-text question answering without a complete graph; graph methods require separate graph construction and validation.

## Remaining Limitations

- Keyword/metric vocabularies do not provide complete semantic retrieval and do not cover every financial metric, language, or table format.
- Comparability of accounting bases across companies, disambiguation of accounting restatements, OCR, understanding of complex charts, and accuracy across the entire corpus require separate evaluation.
