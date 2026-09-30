# Advanced runtime setup and current limits

For the homepage's `build --output` workflow, follow the [QA quickstart](quickstart.md).
That path searches dataset catalogs and uses built-in evidence-processing operators. It does
not invoke the outer Codex worker or require the DataFlow extra. The sections below
cover the separate, advanced acquisition engine and optional integrations.

## Installation profiles

| Profile | Required components |
|---|---|
| Chat workspace (`chat`) | Source checkout, Python 3.10+, built Node.js SDK runner, Responses-compatible model API |
| Prompt → QA (`build --output`) | Python 3.10+, model API with JSON output, network; `.[search]` enables Hugging Face dataset search |
| CLI dry-run / offline demo | Python 3.10+, base Python dependencies |
| Offline tests / package build | `pip install -e '.[dev]'` |
| Hosted-dataset search | `.[search]`, configured model, network |
| Kaggle integrations | `.[search,kaggle]`, Kaggle credentials when required |
| Browser fallback | `.[browser]`, `playwright install chromium` |
| DataFlow pipeline operators | `.[dataflow]`; individual operators may need additional models/dependencies |
| Acquisition worker | Source checkout, Node.js, Corepack, `corepack yarn install --immutable` in `codex-runner/` |

The complete acquisition worker is distributed from the source checkout. A Python
wheel alone does not include the external `codex-runner/` directory or root examples.
Do not advertise the wheel as a self-contained full-runtime distribution.

Configuration lives in `configs/dataflowwebagent.yaml` and the packaged fallback.
Supply `DATAFLOWWEBAGENT_MODEL`, `DATAFLOWWEBAGENT_BASE_URL`, and
`DATAFLOWWEBAGENT_API_KEY` in the environment. Kaggle is an optional dataset-catalog integration for the conversational QA workflow.
Tavily remains available to the separate advanced acquisition engine. No credentials are bundled.

Managed model registration uses `env:` references. For nonstandard providers, a
process environment variable is generated and inherited by the worker; acquisition
start/resume refreshes it. To use that warehouse directly in an unrelated process,
configure an appropriate persistent environment reference yourself.

## Extraction and scale

The generic example requests MinerU at `http://127.0.0.1:7986` and allows legacy
extraction fallback. Configure the service or explicitly select a suitable extraction
backend. Model names in standalone pipeline YAMLs must exist in the warehouse pool;
campaign setup can override them with its resolved model.

Default campaign settings can expand to 24 queries and use four workers. Each crawl
has its own page budget, so these defaults are not a low-cost smoke test. Inspect
`datavoyager dm webagent campaign start --help` and choose smaller settings first.
Campaign task completion and pipeline acceptance are distinct: an empty crawl now
fails, while QA factual correctness still needs a stronger validator.

## Execution and network boundaries

- The outer Codex worker currently requests `danger-full-access` and runs code. Use
  a dedicated container/VM with only the needed credentials and network access.
- HTTP redirects, including robots.txt redirects, are checked before the next hop;
  a rejected private destination cannot trigger browser fallback.
- DNS validation is not connection pinning. Proxy resolution, DNS rebinding, browser
  subresources and browser redirects need stronger egress enforcement. The current
  Playwright path is experimental and is not an SSRF-safe network boundary.
- Agent prompts and untrusted web content require continued prompt-injection work.
  Do not treat prompt instructions as an operating-system sandbox.

## Data quality boundaries

Topic filtering checks relevance signals and source evidence. Generic QA generation
requests grounded answers, but its final validator only checks format. It does not
establish factual correctness. Code validation is not equivalent to passing a complete
test suite. No downstream model improvement is claimed for this alpha.

Source-page manifests contain provenance and license hints, not a rights clearance.
The recorded AIME trial is imported historical material; its offline checker verifies
file counts and selected fields, not a new online run or independent quality audit.

## Troubleshooting

- **Runner unavailable:** install Node/Corepack and the locked runner dependencies.
- **DataFlow import error:** install the `dataflow` extra and the selected operator's dependencies.
- **Unknown model:** configure the environment or register a warehouse model.
- **Existing request:** choose a new `--run`, or inspect/resume the existing acquisition via the advanced CLI.
- **No pages fetched:** inspect `webcrawler_dm_runs/*/failures.jsonl`; failed tasks are retryable.
- **No L2/L3 rows:** inspect the pipeline stage reports and topic rejection reasons.

## Advanced acquisition commands

Without `--output`, `build` uses the legacy advanced acquisition worker to discover hosted
datasets and other resources. This route needs the Node runner and optional integrations
above; it does not automatically produce a single QA training file.

```bash
pip install -e '.[search,browser,dataflow]'
playwright install chromium
cd codex-runner
corepack yarn install --immutable
cd ..

export DATAFLOWWEBAGENT_MODEL="your-model-name"
export DATAFLOWWEBAGENT_BASE_URL="https://your-provider.example/v1"
export DATAFLOWWEBAGENT_API_KEY="your-api-key"

datavoyager build "Collect Python type-error repair examples with explanations" \
  --domain code --focus "type error repair" --target-datasets 2 \
  --warehouse runs/warehouse --run runs/python-repair

datavoyager dm --root runs/warehouse dataset-acquisition-agent status \
  --run runs/python-repair --json

datavoyager badcase --badcase configs/badcase.example.yaml \
  --warehouse runs/warehouse --run runs/badcase-repair --dry-run
```

`--target-datasets` counts source datasets, not training rows. This runner uses the
Responses API; hosted-dataset helpers also use chat-model integrations.

Launch a configurable web pipeline directly:

```bash
datavoyager dm --root runs/warehouse init --json
datavoyager dm --root runs/warehouse webagent campaign start domain_data_acquisition \
  --query "authoritative Python program repair resources" \
  --auto-process --pipeline examples/datamixer_l1_l3_pipeline/pipeline.yaml
```

See `examples/datamixer_l1_l3_pipeline/` for DataFlow, code, and Text-to-SQL recipes.
For a network-free extraction walkthrough, run
`python examples/offline_demo.py --warehouse runs/offline-demo`.
