# Stage datasets and evidence-based QA

A dataset run now produces reusable intermediate artifacts. A QA target counts only candidates that passed model-assisted source review and export deduplication. This is not expert certification or a measured factual-accuracy guarantee.

## Download each stage

The chat workbench exposes **阶段数据** independently of the final QA download. These downloads work during a run and after cancellation or failure, provided the stage has saved records. A live download is a consistent snapshot of committed data, not a promise that the stage is finished.

| Download | Contents |
| --- | --- |
| `raw.jsonl` | Original HTML and acquisition metadata from L1; no text-only conversion |
| `corpus.jsonl` | Selected documents from L2, cleaned text, section segments, relevance decisions and evidence context |
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

## Stop before QA

In chat, say “只采集原始网页，不生成问答” or “整理高血压相关正文，先不要生成 QA”. The controller sets `stop_after` to `raw` or `corpus`. The backend omits the later operators; it does not merely hide their output. Source-only runs use a page budget and do not claim to meet a QA target.

```bash
datavoyager build '收集美国基金费用的投资者教育资料，优先 Investor.gov' \
  --stop-after raw --max-pages 5 --run data/finance-raw-run
datavoyager build '整理成人高血压的 WHO 健康教育正文' \
  --stop-after corpus --max-pages 5 --run data/medical-corpus-run
```

Each run needs a fresh directory. The default `stop_after` is `qa`. Revisions and extensions currently operate on QA versions; source-only runs can provide retained L2 text for a subsequent QA revision.

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

The page budget bounds collected pages, not review calls or token costs. More source sections or candidates add model calls. Reported usage includes selection, generation, quote repair and review. A theoretical six-per-document capacity is only an upper bound: evidence filtering, zero-question outputs, review and deduplication can substantially reduce yield.
