# Stage datasets and evidence-based QA

A dataset run now produces reusable intermediate artifacts. A QA target counts only candidates that passed model-assisted source review and export deduplication. This is not expert certification or a measured factual-accuracy guarantee.

## Download each stage

The chat workbench exposes **阶段数据** independently of the final QA download. These downloads work during a run and after cancellation or failure, provided the stage has saved records. A live download is a consistent snapshot of committed data, not a promise that the stage is finished.

| Download | Contents |
| --- | --- |
| `raw.jsonl` | Records downloaded from Hugging Face and other supported dataset catalogs, with original content and acquisition metadata |
| `merged.jsonl` | Deterministic unified export; whitespace/case-normalized exact-text duplicates are coalesced and source metadata is retained |
| `corpus.jsonl` | Cleaned text, section segments, relevance decisions and evidence context from L2 |
| `source-review.jsonl` | Both accepted and rejected documents, with per-segment decisions and reasons |
| `candidates.jsonl` | All generated QA, exact source segments, claims, review results and original references when repaired |
| Final QA + sources | Only reviewed, deduplicated QA in Alpaca format; a separate manifest retains evidence and review results |

The first four files are written to `<run>/artifacts/` on successful completion. The app also regenerates them on demand from the saved warehouse, so a failed final export does not prevent intermediate downloads. Original QA versions are not overwritten.

For a stopped CLI run, export without making model calls:

```bash
datavoyager export --warehouse data/finance.run/warehouse \
  --stage raw --output data/finance-raw.jsonl
datavoyager export --warehouse data/finance.run/warehouse \
  --stage corpus --output data/finance-corpus.jsonl
datavoyager export --warehouse data/finance.run/warehouse \
  --stage candidates --output data/finance-candidates.jsonl
```

Use a new output filename; the CLI refuses existing destinations. Candidate records have `unreviewed`, `source_supported`, `needs_review` or `duplicate` status. `source_supported` is an intermediate model verdict; final deduplication and cross-source holds can still exclude a candidate. Old, unreviewed QA remain downloadable in their original versions but are not counted as reviewed QA when extending a new version.

## Choose how far to run

The chat stage picker lets users stop after collection, merge, or cleaning; generating QA is a separate full-workflow choice. Collection searches Hugging Face and other supported dataset catalogs. Merge creates a deterministic union and removes exact text duplicates while retaining source metadata. Cleaning extracts readable text and selects relevant evidence. The backend omits later operators; it does not merely hide their output. Source-only runs use a dataset sample-row limit and do not claim to meet a QA target.

```bash
datavoyager build '收集基金费用相关的投资者教育数据集样本' \
  --stop-after collect --max-source-rows 100 --run data/finance-raw-run
datavoyager build '合并基金费用相关的数据集样本' \
  --stop-after merge --max-source-rows 100 --run data/finance-merged-run
datavoyager build '整理成人高血压的 WHO 健康教育正文' \
  --stop-after clean --max-source-rows 100 --run data/medical-corpus-run
```

Each run needs a fresh directory. The default `stop_after` is `qa`. Revisions and extensions currently operate on QA versions; cleaned source runs retain L2 text for a subsequent QA revision.

## Step 3: evidence preparation

1. Remove script/style, navigation, footer and aside content from HTML. Preserve heading, list and table-cell boundaries. Raw HTML remains in L1.
2. Split the full extracted text into sections of at most 5,000 characters. Long paragraphs are split rather than silently discarded after 12,000 characters. All segments remain in the document record.
3. Ask the configured model whether each segment supports the exact requested topic and audience. Require a supporting quote to match the segment. Broad domain membership alone is insufficient.
4. Retain supplied jurisdiction, population, standard and date context only when copied from the segment; unknown context stays empty. This is not an inferred publication date.
5. Archive selection decisions, including rejected documents. Selected L2 documents retain full cleaned text, all segments and the accepted subset for reuse.

## Step 4: generation and review

- Choose up to three selected segments per document, preferring different topic labels. Select zero to two questions per segment for review, hence at most six per document. If a model emits extra candidates, preserve them as `needs_review` with a budget reason rather than aborting or counting them. Unused selected segments remain in L2 and their count is recorded; they are not automatically exhausted in this release.
- Require distinct knowledge points, source claims and exact quotations. Store every candidate before review. If references do not match, first restore omitted spans only when every quoted fragment matches the source in order, then allow at most one quote-repair call while keeping the original references and leaving the answer unchanged. A repaired quote never bypasses review.
- Make a separate review call covering the full question and answer, including topic fit, claim support, numbers, uncertainty, applicability and unresolved conflicts. Validate booleans and exact supporting quotations locally; missing or unsupported evidence is held for review.
- Compare against up to twelve previously accepted candidates available to that worker. Equivalent questions can be marked duplicate; identified incompatible answers under the same scope hold both candidates for review. This bounded comparison is not exhaustive global semantic deduplication or an external guideline search.
- Export checks nonempty QA structure, rejects question=answer, and removes normalized and highly similar questions while preserving different numeric conditions. Only surviving source-reviewed QA count toward the target. Source-only records and legacy unreviewed QA never satisfy that target.

Generation and review currently use the same configured model in separate calls. Reviewers can make mistakes and share the generator's biases. Cross-site verification, expert approval, automatic answer rewriting and global topic quotas are not implemented. A source can itself be wrong or incomplete; a source-supported label does not resolve that risk.

The dataset row limit bounds downloaded source records, not review calls or token costs. More source sections or candidates add model calls. Reported usage includes selection, generation, quote repair and review. A theoretical six-per-document capacity is only an upper bound: evidence filtering, zero-question outputs, review and deduplication can substantially reduce yield.
