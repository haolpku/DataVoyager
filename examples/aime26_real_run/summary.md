# AIME26 + AMO-Bench DataFlow Trial

- Review decision: `release` at 85.00/100 with no redlines.
- Review reports: `pipeline_review.json` and `pipeline_review.md`.
- Trial: 12 input rows, 4 output rows; answer shapes are scalar (1), expression (2), and proof claim (1).
- Field fidelity: all four survivors retain every original input key; zero non-SFT-overwritten values changed. The previously flagged row retains `Answer`, `COT_Reason`, `Question`, `conversations`, and `correct` verbatim.
- Funnel branches: 7 format/target-incompatible rows, 1 quality/difficulty reject, 4 accepted seeds. Schema-recoverable prose/currency/multiple-choice answers are normalized before rejection.
- Full input reserved for upper layer: 20 rows. The chain performs three per-row LLM reasoning generations plus one LLM quality evaluation, with a strict parse retry only on failure. At observed trial latency, the 20-row reserved input is expected to take roughly 20–35 minutes; a large chunked run can take hours.
- Bucket redundancy: the reserved input is a 1.5× candidate pool; expected redundancy is high because elementary/template-incompatible rows are deliberately filtered while exact-answer and proof-capability rows survive.
- Curation status: `not_promoted`. The released pipeline scores 85, below the curator's required 90; no reusable skill or case directory was created or overwritten.
