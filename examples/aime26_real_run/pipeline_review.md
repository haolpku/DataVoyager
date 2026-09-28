# Pipeline Review — Iteration 8

**Decision: release**  
**Score: 85.00 / 100**  
**Redlines: none**

## Dimension Scores

| Dimension | Raw | Weighted | Result |
|---|---:|---:|---|
| D1 Data fit | 3/4 | 11.25/15 | Auditable four-dataset funnel; schema false drops repaired |
| D2 Quality | 3/4 | 18.75/25 | 4/4 correct and clean; calibration passes 5 vs 1 controls |
| D3 Benchmark fit | 3/4 | 15.00/20 | AIME scalar plus AMO expression/proof capability coverage |
| D4 LLM assembly | 4/4 | 12.00/12 | Placeholders, leakage boundaries, and truncation verified |
| D5 Reasoning | 4/4 | 16.00/16 | Durable audit: 4/4 correct processes, 0 wrong-process cases |
| D6 SFT fields | 4/4 | 12.00/12 | Complete 53-field records preserving the 35-key input union |

## Funnel

- Input: 12 rows, three from each of four datasets.
- Seed accepted: 4 rows; rejected rows are explicitly elementary, evaluator-facing/code-form, or below the benchmark difficulty floor.
- Generated/validated: 4 rows; final output: 4 rows (33.3% yield).
- Dataset output shares: 75% and 25%; repository output share is 100% OpenThoughts and is reported explicitly in `trial_funnel.json`.
- Preserved answer shapes: one scalar, two exact expressions, and one proof claim.

## Evidence

- All four final sample IDs pass mathematical equivalence, one-box format, degradation, repetition, truncation, and LLM quality checks.
- Evaluator calibration scores the known-correct control 5 and adversarial wrong control 1.
- Ten official examples were reviewed across the registered AIME 2026 and AMO-Bench datasets.
- Difficulty audit separates retained multi-step analysis/proof rows from rejected elementary or template-incompatible rows.
- Field-fidelity audit checks all four survivors: zero missing original keys and zero changed non-SFT-overwritten values.
- `smp-00026361db14077c` retains `Answer`, `COT_Reason`, `Question`, `conversations`, and `correct` verbatim.

The candidate passes the release gate. No further repairs are required before upper-layer chunked execution.
