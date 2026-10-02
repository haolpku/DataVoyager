# Domain training data Skill: test cases

Run these cases through Codex with `domain-training-data`. Keep the terminal output, manifest, raw files, cleaned files, and per-source reports for review. Tests that only discover data need no model API key. Tests that synthesize or judge QA need an explicitly configured external model and must record its provider-returned usage when available.

## Functional acceptance

| ID | Prompt or setup | Pass criteria |
|---|---|---|
| F1 discovery only | `Find English Hugging Face datasets for machine-learning fundamentals: supervised learning, overfitting, metrics, train/test split. Stop after discovery; do not download or generate QA.` | Returns live catalog candidates rather than a fixed list. Each recommended candidate states ID, data-card/license evidence, language evidence, proposed config/split, schema, and limitation. No raw output file is created. |
| F2 config and split decision | Select a dataset with multiple configs or splits. Ask for a Chinese or English target explicitly. | Codex shows the available configs/splits and the selected field mapping. It does not silently choose the first config or force `train` if the requested content belongs elsewhere. |
| F3 frozen selection | From F1, select two named IDs, configs, and splits. Ask to download 20 source rows in total. | A manifest records exactly those choices and row budgets. The execution uses that manifest; it does not search again or replace a selected source. |
| F4 bounded download | Download `win-wang/Machine_Learning_QA_Collection`, `default/train`, 3 rows. | UTF-8 JSONL and a sidecar report are produced. Every row contains `_source_dataset`, `_source_config`, `_source_split`, and `_source_row`; report count is 3. |
| F5 partial source failure | Use one valid source and one deliberately invalid ID/config in a frozen manifest. | The valid source completes. The invalid source reports dataset ID, config, split, endpoint, and actual error. The result is mixed success, not the misleading message “all downloads failed.” |
| F6 merge and duplicate handling | Supply two tiny sources containing one identical normalized record. Apply exact deduplication. | Output retains provenance for retained rows and reports source rows read, records retained, and duplicate count per source. Raw inputs are not overwritten. |
| F7 quantity semantics | Request “50 retained, deduplicated records” with a source-reading budget of 10. | The report says 10 source rows read and the retained count separately. It does not claim 50 complete; it asks before increasing the reading budget or reports the shortfall/exhausted sources. |
| F8 no-key path | Clear any model API configuration and repeat F1 and F4. | Both discovery/inspection and deterministic download succeed or expose only a source/network failure. They do not fail because an LLM key is absent. |

## Output-quality acceptance

| ID | Prompt or setup | Review rubric |
|---|---|---|
| Q1 relevance | Use F1's machine-learning candidates and inspect 20 retained rows. | At least 16/20 directly concern the requested fundamentals; benchmark perturbations, unrelated quantum-ML material, and generic code dumps are rejected or clearly labelled as unsuitable. |
| Q2 source quality disclosure | Include one dataset with no license or thin card and one well-described source. | The report distinguishes “downloadable/structurally valid” from “factually trustworthy.” Missing license/card is visible and not silently treated as quality approval. |
| Q3 Chinese SFT QA conversion | After approving English ML source records, request: `Convert approved material into Chinese QA; retain English technical terms in parentheses. Produce 20 retained QA pairs.` | Randomly review 10 rows: Chinese is fluent; key terms such as 监督学习 (supervised learning) are preserved; answers are grounded in source content; no empty instruction/output; duplicates are absent. Record that factual correctness still needs subject-matter review. |
| Q4 auditability | Complete any merge-and-clean run. | A reviewer can trace a sampled final record to its source dataset/config/split/row and see the cleaning rule decisions. Rejection counts sum consistently with rows read. |
| Q5 failure transparency | Cause Dataset Server unavailability or use an unsupported private/gated source. | The report gives the precise failing endpoint/status/exception and the unaffected-source result. It does not relabel a transport failure as low content quality. |

## Suggested test order

Run F1 → F2 → F3 → F4 first. They prove the core promise without paid model calls. Then run F5–F8 to exercise error handling and accounting. Use Q1–Q5 as a human review checklist after the mechanics work. Treat the test run as failed when a required artifact or count is missing, even if the chat explanation sounds plausible.
