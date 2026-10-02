---
name: domain-training-data
description: Discover, inspect, download, merge, and clean Hugging Face datasets for domain training data. Use when a user needs to find suitable HF data or prepare selected sources into a documented training corpus; do not use merely to generate QA from user-provided text.
metadata:
  short-description: Prepare domain training data from Hugging Face
---

# Domain training data

Use this skill to prepare a reliable, traceable corpus, with Hugging Face as
the primary catalog. Codex is the conversation layer: do not create a second
chat agent or require an LLM API key for discovery, inspection, download,
merge, or deterministic cleaning.

Treat the work as three independently completable stages. Stop after discovery
when the user asks to find data only. Do not download a source until the user
has selected it, except for a small, explicitly labelled preview when they ask
for one.

1. **Find data.** Search the real HF catalog; read the dataset card and inspect
   available configs, splits, schema, language/task tags, license metadata, and
   a bounded sample. Report candidates with the proposed config/split, field
   mapping, availability confidence, limitations, and recommendation reason.
   A tag or nonempty row is not evidence that the facts in a dataset are true.
2. **Merge data.** Freeze the user-confirmed dataset IDs, revisions, configs,
   splits, and field mappings in a manifest before downloading. Preserve a
   source identifier and source-row URI on every record. Normalize into a raw
   canonical schema, then merge and exact-deduplicate; report counts per source.
3. **Clean data.** Agree the null, duplicate, language, relevance, length, and
   target-format rules before applying them. Keep an audit report with every
   rejection reason. Convert to SFT/QA only when the user requests it.

Read [workflow.md](references/workflow.md) before operating a stage. Read
[reporting.md](references/reporting.md) when producing a manifest or delivery
report. Use `scripts/hf_dataset.py` for deterministic catalog inspection and
bounded Dataset Server downloads; it does not import the project's fragile
`datasets` runtime.

## Quantity and quality

Always state whether a requested number means downloaded source rows, retained
records, or valid/deduplicated QA pairs. For a target of retained records, do
not report success from the number read. Continue to the target only when the
user authorizes the extra source-reading budget; otherwise report the shortfall
and exhausted sources.

Keep structural availability separate from factual quality. Dataset card,
license, source provenance, and nonempty fields establish only metadata and
format; domain claims need an appropriate review plan, especially for medical,
legal, or safety-critical training data.

## Boundaries

Do not silently substitute a new dataset, configuration, language, split, or
license when a selected source fails. Report the source-specific failure and
ask whether to change the frozen manifest. License labels are metadata, not
legal advice. If an external model is used for optional synthesis or judging,
report provider-returned usage when available; never claim an exact internal
Codex token cost.
