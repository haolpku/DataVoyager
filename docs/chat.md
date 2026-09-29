# Chat workspace

Start with a request, inspect samples, and refine the dataset in the same conversation.
The chat controller uses Codex SDK. Dataset collection and processing run in Python
background processes so you can continue discussing a running job.

## Start locally

macOS or Linux, Python 3.10+, Node.js 22+, and Corepack:

```bash
git clone https://github.com/haolpku/DataVoyager.git
cd DataVoyager
python -m venv .venv
source .venv/bin/activate
pip install -e .
cd codex-runner
corepack yarn install --immutable
corepack yarn build
cd ..
datavoyager chat
```

Open `http://127.0.0.1:8765`. Under **连接模型 API**, enter the base URL, model name,
and API key. The controller requires a Responses-compatible endpoint supported by
Codex. Dataset generation can use Responses or Chat Completions on the same endpoint.
A chat-only endpoint cannot power the Codex controller.

No authentication or paid model call is made merely by opening the page or saving
settings. The first message starts the controller. The UI's configured indicator means
settings are present, not that a provider connection has been tested.

Existing `DATAVOYAGER_MODEL`, `DATAVOYAGER_BASE_URL`, and `DATAVOYAGER_API_KEY`
environment variables can preconfigure the app. For an all-Responses endpoint, use
`DATAVOYAGER_API_FORMAT=responses`. Keys entered in the interface are held only in the
server's memory and must be entered again after restarting. Base URL and model settings
are saved; keys are not stored in browser storage or configuration files.

## A conversation

```text
You: Create Chinese QA for beginners learning Python generators. Prefer the official docs.
You: Focus on common misconceptions and include small code examples.
You: What has finished, and how much API usage has been reported?
```

The agent chooses among a reply, new collection, revision, and explicitly requested additional collection.
Its complete accumulated request is saved with each version. Initial sample runs
normally use a five-page budget. A page budget is not a QA quota or a monetary cap.
Explicit requested QA counts are stored separately as `target_rows`. The interface
shows generated unique questions against this target. New runs count only source-reviewed and deduplicated QA; model review is not expert certification.
The collection budget does not restrict the number of navigation choices: discovery
can inspect up to 50 links per page, even for a one-page sample. Link ranking helps
navigation; it does not replace the model's source-quality judgment.

- **New collection:** discovers sources and creates an independent version. It does
  not automatically append to or merge with old versions.
- **Revision:** copies accepted text from a prior version and regenerates QA with
  updated requirements. No new crawl is needed; previous outputs are unchanged.
- **Additional collection:** after a shortfall, explicitly add a page budget. A new
  version copies the previous QA and accepted sources, then searches under the same
  requirements to fill the remaining count. Earlier questions are retained first.
- **During a run:** ask questions or discuss changes. There is at most one active
  dataset job per conversation. Changes apply to a later version; they do not rewrite
  an in-flight generation. To change direction immediately, stop the job first.

Up to five source-reviewed QA candidates appear in the preview, with source links.
Candidates can appear before final export. Completed versions provide QA and source
manifest downloads. Final exports require source-review approval and question deduplication. Intermediate downloads remain available even if no QA passes review. See [stage datasets](evidence-pipeline.md) for review limits and source-only runs.

## Requested counts and confirmation

For example: “生成 100 条中文金融问答，最多采集 5 页。” The current operator
generates at most six QA candidates per document, so the backend pauses before collection
and explains that this budget supports at most 30 rows, with actual yield possibly lower.
Edit the target or page budget in the confirmation card, cancel, or explicitly try
the existing budget. This upper bound is not a quality or yield prediction. The chat
controller turn may consume API usage; the paused dataset build has not started.

When the target fits the budget, the build starts directly. Count-aware builds search
in up to five rounds within the total page budget. They stop when the requested number
of distinct questions is reached, the allocated budget or round limit is reached, or
two consecutive rounds produce no new questions. Unused allocation from a round is
not reclaimed. Discovery/search navigation requests are separate from this collection
budget; it is not an API-spending limit. Source and topic restrictions stay in place.

A shortfall has status `needs_confirmation`, not `completed`. The worker exits and
the interface offers three paths:

- Accept the current count without further generation. The original target and gap
  remain in the report; status becomes `accepted_partial` and `target_met` stays false.
- Add an explicit page budget and continue in a new version, retaining existing QA.
- Discuss a different topic or source scope and start a new independent build.

Partial exports can be previewed and downloaded with their shortfall status. Zero-QA runs cannot be accepted as partial QA, but their saved intermediate datasets can still be downloaded. Confirmation is checked by the server, survives
restarts, and cannot be replayed to launch the same job twice. Changing requirements
invalidates an unconfirmed plan. Running tasks are still interrupted on server restart;
they do not resume automatically. Provider or pipeline failures remain failures, not
quantity confirmations.

The CLI accepts `--target-rows` and `--max-rounds`, and recognizes simple explicit
Chinese/English counts in the request. It attempts the supplied budget without an
interactive prompt, writes any available partial export, and exits with code **2** for
`needs_confirmation` (0 for completion, 1 for failure):

```bash
datavoyager build '中文金融基础知识问答，优先参考公开投资者教育资料' \
  --target-rows 100 --max-pages 150 --max-rounds 5 --output data/finance-qa.jsonl
```

## Progress and usage

The browser refreshes backend snapshots every two seconds. It does not use the agent's
narration as a progress counter. Select a version to see its pages, accepted sources,
QA candidates, stage, and data-generation usage.

Main-agent usage is displayed for the whole conversation as **turns and reported tokens**.
The SDK does not expose a reliable count of its internal HTTP attempts here. Dataset
usage is displayed per version as **API attempts and reported tokens**, including retries.
On thread continuation, the pinned SDK returns cumulative thread tokens; the server
persists a per-thread baseline and records only the increment for each turn.
These are separate accounting units; they are not added into a misleading “total calls”.
Missing or pending usage is marked partial, and cost is not estimated. Search-provider
charges are not included. Stopped runs retain their last snapshot and finished-call log;
usage for an interrupted in-flight request may be unavailable.

## Versions and recovery

Each crawl writes completed tool steps to
`warehouse/webcrawler_dm_runs/<crawl-id>/trace.jsonl` within its version directory.
The trace records tool arguments, observations, and reported errors after each step,
so completed steps survive discovery failures or cancellation. An in-flight tool
may not have a recorded result. The configured model key is redacted. Traces contain
search queries and webpage excerpts; review them before sharing.

By default the app saves state in `runs/chat`:

```text
sessions/<conversation-id>/
  state.json                      # messages, SDK thread ID, version index, agent usage
  versions/<version-id>/
    job.json                      # action and complete requirements, no credentials
    qa.jsonl                      # completed training export
    qa.jsonl.sources.jsonl        # row-level provenance
    run/                          # progress, API call log, report
    warehouse/                    # raw sources, accepted text, QA candidates
```

Reloading the page preserves the conversation and selected version records. Restarting
the server restores conversations and SDK thread IDs; previously running jobs are
marked interrupted. Automatic job resume is not implemented. You can create a revision
from retained accepted text or start a fresh collection.

**停止当前任务** terminates the dataset worker and retains collected files. Closing the
browser tab does not stop the server or a job. Ctrl+C in the server terminal stops
active local workers. The workspace is exclusively locked to one server process.

```bash
datavoyager chat --root runs/my-workspace --port 8765
```

The server binds only to `127.0.0.1`. This release is a local single-user application.
The SDK runner must be built from a source checkout; a Python wheel by itself does
not include it. An advanced install can point `DATAVOYAGER_CHAT_RUNNER` at a built
`codex-runner/dist/chat.js`.

## Verification

Offline tests include real SDK/CLI thread continuation against a local mock Responses
server, plus structured decisions, usage events, HTTP
boundaries, persistence, cancellation, and a real revision subprocess using a local
mock model API. Browser checks exercise the interface with explicit test fixtures.
Run the SDK transport check after building the runner:

```bash
DATAVOYAGER_TEST_SDK=1 python -m pytest tests/test_chat_sdk.py -q
```

These tests do not establish live-provider compatibility or generated-answer quality.
