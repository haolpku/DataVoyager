# DataVoyager

**Turn a prompt into a QA training dataset.**

Describe what you want to teach your model. DataVoyager searches dataset catalogs, with Hugging Face as the primary source and optional Kaggle support. Dataset records go through evidence review before becoming question–answer pairs. You get a JSONL training file, source records, and a report of model API usage.

[中文](README_zh.md) · [Usage guide](docs/quickstart.md) · [Architecture](docs/architecture.md)

![DataVoyager chat workspace](docs/assets/chat-workspace.png)

## Build it in a conversation

```text
You: Make beginner QA about Python generators. Start with a small sample.
You: Focus more on common misconceptions, with fewer definition questions.
You: This version looks good. Let me download it.
```

The local chat workspace keeps the conversation, sample previews, source progress, usage, and dataset versions together. Changes to question style or language can reuse collected source text; a new topic starts a new collection. Previous versions remain available. Install `.[search]` to enable Hugging Face discovery. When the optional Kaggle integration is installed and configured, it can provide another dataset source.

After installing the Python project below, build the chat controller with Node.js 22+ and Corepack:

```bash
cd codex-runner
corepack yarn install --immutable
corepack yarn build
cd ..
datavoyager chat
```

Open **http://127.0.0.1:8765**, enter your API endpoint, model, and key, and start chatting. The conversational controller uses **Codex SDK** and requires a Responses-compatible endpoint. See the [chat workspace guide](docs/chat.md).

## Or start from the command line

> Create a QA dataset about Python generators for beginners. Write answers in English and include short code examples. Prefer official Python documentation.

```bash
datavoyager build "Create a QA dataset about Python generators for beginners. Write answers in English and include short code examples. Prefer official Python documentation." \
  --output data/python-qa.jsonl
```

The output uses the Alpaca format. An illustrative row:

```json
{
  "instruction": "What happens when you call a Python generator function?",
  "input": "",
  "output": "It returns a generator iterator; the function body does not run yet. For example:\n\ndef numbers():\n    yield 1\n\ng = numbers()\nprint(next(g))  # Runs until yield and prints 1"
}
```

The same command can collect material for product-support questions, course exercises, or a specialist knowledge assistant. Change the topic, audience, and answer style in your request.

## Bring your API

Requires Python 3.10+ and a model endpoint that supports Chat Completions with JSON output.

```bash
git clone https://github.com/haolpku/DataVoyager.git
cd DataVoyager
python -m venv .venv
source .venv/bin/activate
pip install -e .

export DATAVOYAGER_BASE_URL="https://your-provider.example/v1"
export DATAVOYAGER_MODEL="your-model-name"
export DATAVOYAGER_API_KEY="your-api-key"
```

Run the request above. The default downloads up to 20 dataset rows; use `--max-source-rows` to change the sample limit. No separate browser, Node.js runtime, or DataFlow installation is needed for this workflow. Responses API endpoints are also supported via `DATAVOYAGER_API_FORMAT=responses`.

## Watch it work

```text
Request → Find sources → Merge & deduplicate → Clean & filter → (optional) Generate QA
```

The chat UI lets you choose whether a run stops after finding data, merging sources, cleaning evidence, or continues through QA generation. The terminal updates every two seconds with the current stage, downloaded source rows, accepted sources, QA candidates, model API calls, and reported input/output tokens.

Example progress display (illustrative numbers):

```text
[42s] generating QA | source rows 80 | accepted sources 5 | QA 3 | API calls 14 (0 failed, 1 active) | tokens 18200 in / 2100 out (partial; some usage unavailable or pending)
```

Check the latest snapshot from another terminal:

```bash
datavoyager status --run data/python-qa.jsonl.run
```

## Take the dataset with you

| File | Contents |
|---|---|
| `data/python-qa.jsonl` | QA pairs with `instruction`, `input`, and `output` fields |
| `data/python-qa.jsonl.sources.jsonl` | Source URL and metadata for each exported row |
| `data/python-qa.jsonl.run/report.json` | Final row count, elapsed time, and model API usage |

The pipeline selects topic evidence, generates QA with references, and makes a separate model-assisted source review. Only reviewed, deduplicated QA count toward the target; this is not expert certification. Generated answers still need review before training. Token counts come from provider responses; missing usage is flagged, and monetary cost is not inferred. See the [usage and quality notes](docs/quickstart.md#what-the-numbers-mean).

---

[Advanced acquisition & DataFlow pipelines](docs/runtime.md) · [Roadmap](docs/roadmap.md) · [Attribution & licensing](THIRD_PARTY_NOTICES.md)

Developer preview. A project-level license has not yet been assigned.

Intermediate datasets are independently downloadable, including after cancellation. You can also stop after raw collection or corpus preparation. See [stage datasets and evidence-based QA](docs/evidence-pipeline.md) for the new workflow and model-review limitations.
