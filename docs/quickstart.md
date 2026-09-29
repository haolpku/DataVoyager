# Prompt → QA dataset

The homepage command uses a Python-only workflow. You provide a model API and a natural-language request; the run searches the web, collects HTML, filters text, generates QA, and exports Alpaca JSONL.

## Model configuration

Set all three variables:

```bash
export DATAVOYAGER_BASE_URL="https://your-provider.example/v1"
export DATAVOYAGER_MODEL="your-model-name"
export DATAVOYAGER_API_KEY="your-api-key"
```

The default protocol is Chat Completions with JSON output. For a Responses endpoint:

```bash
export DATAVOYAGER_API_FORMAT=responses
```

The selected model must support the request parameters and JSON output used by the chosen protocol. An endpoint described as “OpenAI compatible” may support only one of them. The key is read from the environment; the warehouse stores an environment reference.

Existing warehouse models and `DATAFLOWWEBAGENT_*` configuration remain supported when none of the three `DATAVOYAGER_*` credentials variables is set. The legacy flat configuration uses Responses.

Web search has public search backends. `TAVILY_API_KEY` is optional; availability of public search and source websites depends on your network. Search-service billing is separate from model usage.

## Run

```bash
datavoyager build "Create beginner QA about Python generators. Answer in English, with short code examples. Prefer official Python documentation." \
  --output data/python-qa.jsonl --max-pages 20
```

Specify the topic, intended audience, language, and answer style in the request. Source preferences guide discovery; they are not an enforced domain allowlist. The entire request reaches QA generation.

The run directory defaults to `<output>.run`, and its warehouse to `<output>.run/warehouse`. Override them with `--run` and `--warehouse`. Use fresh output and run paths for a new request. Existing outputs are never overwritten.

`--max-pages` bounds pages collected by the crawl, not QA count, discovery page inspections, API calls, or money spent. The quick path uses one search task with a bounded discovery loop. It generates at most six candidates per accepted document, then counts only source-reviewed and deduplicated QA. A requested sample count is not a guaranteed quota.

`--domain` and repeated `--focus` provide extra filtering hints. `--keywords` and `--target-datasets` belong to the advanced acquisition workflow without `--output`.

Preview a request without credentials, network calls, or writes:

```bash
datavoyager build "Beginner Python QA" --output data/python-qa.jsonl --dry-run
```

## Monitor a run

Progress prints to stderr every two seconds; the final JSON report prints to stdout. The latest snapshot is also written atomically to `progress.json`:

```bash
datavoyager status --run data/python-qa.jsonl.run
datavoyager status --run data/python-qa.jsonl.run --json
```

This reads the last saved snapshot; it does not attach to or resume the process. The JSON includes its update timestamp and process ID. If a process is forcibly killed, its last snapshot may still say `running`.

The display shows active processing stages, collected pages, accepted source documents, QA candidates, elapsed time, and model usage. Collection and generation overlap. Counts reflect committed warehouse records and can advance in batches; QA candidates may exceed the final export after validation and deduplication. No percentage or finish-time estimate is invented.

## What the numbers mean

- **Calls:** actual model request attempts, including retries and failures, across discovery, classification, and generation. In-flight requests are shown separately.
- **Input/output tokens:** sums of usage reported by the model provider, for both Chat Completions and Responses formats. An incomplete response still contributes its reported usage.
- **Missing usage:** failed requests or providers that omit usage are counted in `calls_without_usage`. The totals are partial when usage is missing or calls are still in flight. Zero reported tokens does not imply zero billing.
- **Cost:** left unset. Provider prices, cache discounts, and search-service charges are not inferred. Model accounting excludes search APIs and web traffic.

`api_calls.jsonl` records per-attempt model name, outcome, elapsed time, and reported token counts. It does not record credentials or prompt/response bodies. `report.json` retains accumulated usage on success and handled failure.

Source text is cleaned and split into sections, selected against the exact topic, and used for evidence-backed QA generation. A separate model call reviews each candidate. Export requires source-review approval, nonempty questions and answers, and question deduplication. See [stage exports and evidence processing](evidence-pipeline.md) for stopping before QA, downloading intermediate data and review limitations.

Model source review is not independent expert verification. Bounded semantic comparisons do not catch every duplicate or cross-source conflict, and the pipeline does not verify licensing. Review samples and source permissions before training or redistribution. There is no benchmarked accuracy or throughput claim.

## Files

| Path | Contents |
|---|---|
| `<output>` | Training rows: `instruction`, `input`, `output` |
| `<output>.sources.jsonl` | Output row, candidate ID, source segment, claim evidence and review |
| `<output>.run/artifacts/` | Raw pages, selected corpus, source-selection decisions and QA candidates |
| `<output>.run/request.json` | Original request and crawl budget |
| `<output>.run/progress.json` | Latest progress and usage snapshot |
| `<output>.run/api_calls.jsonl` | Finished model request attempts, including retries |
| `<output>.run/report.json` | Final summary or handled failure |
| `<output>.run/campaign.json` | Campaign and pipeline results |
| `<output>.run/warehouse/` | Raw pages, intermediate text, generated samples, and pipeline state |

## Troubleshooting

- **Authentication or protocol error:** check the API key, model name, base URL, and `DATAVOYAGER_API_FORMAT`.
- **No search results:** check network access or configure `TAVILY_API_KEY`.
- **No accepted QA:** inspect `campaign.json` and warehouse stage reports. Narrow the request or use better sources. An empty result is an error and does not produce an empty “successful” training file.
- **JavaScript-only pages:** this workflow uses HTTP extraction; those pages may require the [advanced browser setup](runtime.md).
- **Interrupted run:** inspect its saved progress and usage; rerun with a new output path. Quick-path automatic resume is not implemented.

## Development checks

```bash
pip install -e '.[dev]'
python -m pytest -q
python -m build
```

Tests cover real extraction, filtering, warehouse writes, generation orchestration, export, and usage accounting with mocked external web/model responses. They do not establish live-provider compatibility or answer quality.
