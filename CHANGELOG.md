# Changelog

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
