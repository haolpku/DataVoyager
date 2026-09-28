# DataVoyager

### From natural language to traceable domain datasets

**Describe the dataset. Let agents discover, collect, and curate it.**

[中文说明](README_zh.md) · [Architecture](docs/architecture.md) · [Runtime & limitations](docs/runtime.md) · [Roadmap](docs/roadmap.md)

DataVoyager connects natural-language data requirements to hosted-dataset discovery,
web acquisition, and configurable processing pipelines. It brings source records,
derived samples, quality decisions, and lineage into one local warehouse.

> **Alpha: `0.1.0a1`.** The current release is a developer preview. It includes an
> acquisition runtime and offline regression tests; it is not a benchmarked guarantee
> of dataset quality or downstream model improvement. The default QA validator checks
> structure, not factual correctness. See [limitations](docs/runtime.md).

```text
"Build a Python type-error repair dataset for beginners"
                         │
              Natural-language request / badcase report
                         │
                 Acquisition worker
                 ┌───────┴────────┐
          Hosted datasets     WebAgent campaigns
          Search / download    Search / inspect / crawl
                 └───────┬────────┘
                    DataMixer warehouse
                         │
         DataFlow + custom operators + model calls
                         │
             Domain datasets + reports + lineage
```

## What is implemented

- **Natural-language entry point:** `datavoyager build` persists the complete request
  and passes it to the existing acquisition worker. Optional domain and focus hints
  guide discovery; this is not yet a separate, schema-enforced dataset-spec compiler.
- **Two discovery routes:** search hosted datasets (including Hugging Face and optional
  Kaggle), and run domain-focused web campaigns.
- **Bounded web exploration:** an LLM searches, inspects pages, extracts links, and
  submits resource URLs; a crawler then collects raw HTML.
- **Configurable curation:** HTML extraction, filtering, topic classification, and
  optional QA/code/Text-to-SQL generation through operator pipelines.
- **Persistent execution:** campaign queues, retries, progress, streaming processing,
  source metadata, and lineage.
- **Badcase-driven acquisition:** use a failure report as the request, retaining the
  original report for the worker to inspect.

The webpage pipeline uses **L1 = raw HTML**, **L2 = processed text**, and
**L3 = initial SFT samples**. A completed acquisition may contain several datasets;
the advanced DataMixer recipe/export commands control the final training export.

## Quick start: no credentials needed

Use Python 3.10+ from a source checkout:

```bash
git clone https://github.com/haolpku/DataVoyager.git
cd DataVoyager
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'

datavoyager build "Build a Python type-error repair dataset for beginners" \
  --domain code --focus "type error repair" --target-datasets 2 --dry-run

python examples/offline_demo.py --warehouse runs/offline-demo
python -m pytest -q
```

The dry run prints the request without network calls or file writes. The offline demo
uses two authored HTML fixtures and runs real extraction, L1/L2 storage, and lineage.
It does **not** simulate web acquisition or claim to generate validated SFT data.

The Python distribution/import name remains `dataflowwebagent` for compatibility;
the new user-facing command is `datavoyager`. Existing commands continue to work.

## Run acquisition with models and network access

The acquisition worker uses the bundled **Codex SDK runner** and can execute shell
commands. Use a dedicated execution environment with scoped credentials. The current
worker requests `danger-full-access`; a run directory is not a security sandbox.

Install the optional components you need:

```bash
python -m pip install -e '.[search,browser,dataflow]'
playwright install chromium

# Node.js and Corepack are required for the acquisition worker.
# Use a current Node.js LTS runtime; install Corepack if your distribution omits it.
cd codex-runner
corepack yarn install --immutable
cd ..

export DATAFLOWWEBAGENT_MODEL="your-model-name"
export DATAFLOWWEBAGENT_BASE_URL="https://your-provider.example/v1"
export DATAFLOWWEBAGENT_API_KEY="your-key"
# Optional, for Tavily-backed search:
export TAVILY_API_KEY="your-tavily-key"

datavoyager build "Collect Python type-error repair examples with explanations" \
  --domain code --focus "type error repair" \
  --target-datasets 2 \
  --warehouse ./runs/warehouse --run ./runs/python-repair
```

`--target-datasets` counts source datasets, **not rows**. A run directory belongs to
one request; use a new directory for another build. The provider must support the
Codex runner's Responses API; hosted-dataset helpers also use chat-model integrations.
Arbitrary OpenAI-compatible endpoints are not guaranteed to support both.

Inspect a run:

```bash
datavoyager dm --root ./runs/warehouse dataset-acquisition-agent status \
  --run ./runs/python-repair --json
```

Run from a badcase report:

```bash
datavoyager badcase --badcase configs/badcase.example.yaml \
  --warehouse ./runs/warehouse --run ./runs/badcase-repair --dry-run
```

Direct web campaign, bypassing the outer acquisition worker:

```bash
datavoyager dm --root ./runs/warehouse init --json
datavoyager dm --root ./runs/warehouse webagent campaign start domain_data_acquisition \
  --query "authoritative Python program repair resources" \
  --auto-process --pipeline examples/datamixer_l1_l3_pipeline/pipeline.yaml
```

The example pipeline references a MinerU service with a legacy extraction fallback.
It also needs `open-dataflow` and model access. See [runtime setup](docs/runtime.md)
before launching a full campaign; its default settings are larger than a smoke test.

## Repository map

| Path | Purpose |
|---|---|
| `src/dataflowwebagent/voyager.py` | Natural-language CLI |
| `src/dataflowwebagent/badcase_pipeline.py` | Report adapter |
| `src/dataflowwebagent/skills/ObtainerCLI/` | Acquisition worker, dataset search, download, export |
| `src/dataflowwebagent/agents/Obtainer/datamixer/` | Storage, operators, pipelines, model registry |
| `…/datamixer/webagents/` | WebAgent discovery, crawling, campaigns |
| `codex-runner/` | Node.js bridge to the Codex SDK |
| `examples/offline_demo.py` | Network-free L1 → L2 walkthrough |
| `examples/datamixer_l1_l3_pipeline/` | Generic, code, and Text-to-SQL pipeline examples |
| `examples/aime26_real_run/` | Imported recorded trial; historical evidence, not a current benchmark |
| `tests/` | Offline regression coverage |

## Scope and next steps

Quality is a set of inspectable checks, not a property guaranteed by the project name.
The next milestones are a structured Dataset Spec, stronger domain validators,
global cost budgets, and reproducible end-to-end evaluations. See the [roadmap](docs/roadmap.md).

This project is separate from the DataVoyager research prototype introduced in
[Data-driven Discovery with Large Generative Models](https://arxiv.org/abs/2402.13610),
which studies hypothesis generation and analysis over existing datasets.

## Attribution and licensing status

This repository imports the supplied DataflowWebAgent codebase and keeps its runtime
module names. DataFlow operators are provided by [OpenDCAI/DataFlow](https://github.com/OpenDCAI/DataFlow).
The web kernel notes inspiration from [browser-use](https://github.com/browser-use/browser-use).
Bundled assets and source-page fixtures retain their existing notices and provenance.

The imported archive did not include a project-level license. A project-wide license
has not been assigned in this initial import; resolve it with the code owners before
an open-source release. See [third-party notices](THIRD_PARTY_NOTICES.md).
