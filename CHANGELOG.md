# Changelog

## Unreleased

- Add reproducible finance/medical QA evaluation, fixed-source language/style controls, source-quote audits, and reported API accounting; publish the 100/1000-target baseline results and limitations.
- Allow model configurations to omit unsupported sampling parameters for reasoning-model audits.
- Separate the QA crawl budget from discovery candidates; a one-page run can inspect up to 50 links per page.
- Recognize sibling subdomains using the bundled public suffix list, and rank link candidates by query overlap and documentation navigation before truncating.
- Write each WebAgent tool step to `trace.jsonl`, retaining diagnostics when discovery fails or is interrupted.
- Add a local chat workspace with Codex SDK conversation continuation and structured action dispatch.
- Persist conversations and dataset versions, with live progress, source-linked previews, downloads, and task cancellation.
- Reuse accepted source text for QA revisions without modifying previous versions.
- Separate conversation token usage from dataset model API accounting.
- Keep API keys in process memory and pass them to isolated workers through environment variables.
- Add direct prompt-to-QA export, strict pair validation, source manifests, and API usage reports.

## 0.1.0a1 — Initial DataVoyager import

- Import DataflowWebAgent acquisition, storage, crawling and pipeline modules.
- Add `datavoyager build` with a no-write dry run and complete request persistence.
- Preserve full badcase reports when launching acquisition.
- Validate HTTP redirect targets before requests, including robots.txt redirects.
- Prevent rejected private redirects from triggering browser fallback.
- Fail empty crawls instead of marking them successful.
- Store managed model keys as environment references.
- Add an authored offline HTML-to-L2 example and regression coverage.
- Document architecture, setup, current limits, attribution and release roadmap.
