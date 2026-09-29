# Evidence pipeline smoke test — 2026-09-29

This checks the new section-selection, QA-generation, review and stage-export implementation. It is not a factual-accuracy benchmark, a new 100/1,000-row evaluation, or a test of the complete conversational acquisition flow.

## Setup

- Reused one previously collected finance HTML page (SEC/Investor.gov fund fees) and one medical HTML page (WHO hypertension).
- Started from raw HTML and ran the new pipeline using the configured provider's `gpt-4.1` model. Generation and review used separate calls to the same model.
- No new web discovery or expert review was performed. API credentials and provider connection details are excluded from this report.
- Local final artifacts: `runs/evidence-smoke-20260929-v3/`, including each domain's report, four stage exports, final QA and source manifest. These run files are not committed.

## Final run

| Domain | Raw pages | Selected documents | QA candidates | Source-supported QA exported | Held for review | API calls | Tokens | Seconds |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Finance | 1 | 1 | 3 | 1 | 2 | 10 | 11,037 | 40.0 |
| Medical | 1 | 1 | 3 | 1 | 2 | 10 | 11,651 | 50.5 |

Both runs completed and exported all intermediate datasets. Only reviewed candidates entered the final QA files. Held candidates, evidence and review decisions remained separately downloadable. The provider reported token usage for all 20 calls; monetary cost was unavailable.

Earlier development runs exposed two integration issues: references containing omitted passages failed exact matching, and a model returning more than two candidates per segment aborted the pipeline. The implementation now restores a contiguous source span only when all quoted fragments occur in order, without changing review verdicts, and retains excess candidates with a budget reason instead of aborting or counting them. One bounded reference-repair call can also run without rewriting the answer. Across all three development iterations, 52 API calls consumed 61,740 reported tokens; the earlier medical candidate-count failure was an application validation failure, not an HTTP failure.

## Interpretation

The final two-page sample demonstrates that candidate retention, source review and stage exports work with real model responses. Two exported rows out of six candidates is a yield observation, not a measured accuracy rate or evidence of stable quality improvement. It also shows that achieving a requested dataset size remains difficult under a fixed page budget. The system must report shortfalls and preserve the confirmation flow rather than count held candidates toward the target.

Review can miss unsupported claims or reject valid paraphrases. The bounded comparison against earlier candidates is not exhaustive cross-source verification. Larger domain evaluations and expert adjudication remain necessary before claiming training quality or consistent quantity delivery.

## Regression checks

- Python suite: 95 passed, 1 skipped.
- TypeScript controller: build succeeded; 4 tests passed.
- Python wheel: isolated build succeeded.
- Local workbench: stage links and existing QA downloads remained available; raw and corpus downloads returned saved data for an existing run.
- Automated tests cover source-only stopping, rejected-evidence retention, quote validation, cross-source holds, legacy QA exclusion and stage downloads after cancellation.
