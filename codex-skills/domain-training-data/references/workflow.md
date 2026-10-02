# Workflow

## Discovery

Clarify the domain, target language, intended format, license constraints, and
quantity definition if they matter to the request. Search Hugging Face rather
than returning a static catalog. Exclude benchmark perturbations and unrelated
near-keyword results only after checking their task/card; do not make a broad
keyword filter the sole quality test.

For each serious candidate, inspect it with:

```powershell
python scripts/hf_dataset.py inspect owner/dataset
python scripts/hf_dataset.py rows owner/dataset --config CONFIG --split SPLIT --limit 3
```

`inspect` writes no files. It prints card metadata and every Dataset Server
config/split. `rows` requires an explicit config and split, preventing an
English-first or `default/train` guess from being treated as a valid selection.
If the Dataset Server is unavailable, say that availability is unverified; do
not call the dataset unusable without reporting that distinction.

Candidate reports should name the data ID, license tag (or absence), language
evidence, task/schema, rows available, proposed config/split, a small sample
when requested, and the reason it fits or does not fit. Do not claim factual
correctness merely because the source is downloadable.

## Source confirmation and download

After the user chooses sources, create a manifest containing their exact IDs,
optional revisions, chosen configs/splits, field mapping, and the row budget.
Do not redo catalog search and match by ID later. This freezes the decision
that the user reviewed.

Download one selected source with:

```powershell
python scripts/hf_dataset.py download owner/dataset --config CONFIG --split SPLIT --limit 1000 --output .\data\raw\owner__dataset.jsonl
```

The downloader emits UTF-8 JSONL with `_source_dataset`, `_source_config`,
`_source_split`, and `_source_row` on every row and writes a sidecar report.
It is deliberately bounded: Dataset Server is suitable for inspection and
moderate row reads, not an unbounded replacement for an artifact downloader.
For large sources, use a dataset-native artifact downloader after the config,
split, and files have been validated.

If multiple selected sources are used, allocate the total source-row budget
explicitly (equal shares by default) and record that allocation. A source
failure must retain its HTTP/error detail in the report while the remaining
sources continue when safe.

## Merge and clean

Normalize only after examining the selected schema. Preserve original columns
in the raw layer and add canonical fields rather than overwriting source data.
Use a source-specific adapter when question/answer, document, or chat fields
differ; validate the adapter on sample rows before a full run.

Perform cleaning as explicit, reportable rules: empty-field removal, exact or
normalized duplicate removal, language/relevance filtering, length limits, and
optional SFT mapping. Report read, retained, rejected by rule, duplicate, and
failed counts per source. Keep raw data, cleaned data, manifest, and reports
as separate artifacts.
