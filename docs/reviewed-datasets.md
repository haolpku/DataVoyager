# Reviewed first-step datasets

Finance and medical discovery uses this reviewed list instead of promoting arbitrary keyword-search results. Each entry was checked against its Hugging Face card for task, language, approximate size, schema, license label, and limitations. The current Obtainer downloader was also used to read small samples for the datasets marked verified. This is a shortlist, not an endorsement or a guarantee that every row is correct.

## Finance

- [whpthomas/finqa-parquet](https://huggingface.co/datasets/whpthomas/finqa-parquet) — 8,281 English rows, MIT tag. It is financial-report evidence text, not a question/answer pair schema: each row has one `text` field with report text and a question. Its card says the original train, dev, and test data are combined, so it is not suitable as a held-out evaluation split. The current downloader successfully read a sample.
- [Akhil-Theerthala/PersonalFinance_v2](https://huggingface.co/datasets/Akhil-Theerthala/PersonalFinance_v2) — about 7,036 English rows, Apache-2.0 tag, with `category`, `query`, `chain_of_thought`, and `response`. It covers personal situations such as debt, retirement, investment, taxes, and budgeting. Its card says answers are produced through a multi-stage model-assisted process; it is US-centric personalized advice, not a current factual finance reference. The current downloader successfully read a sample.

## Medical

- [Bolin97/MedicalQA](https://huggingface.co/datasets/Bolin97/MedicalQA) — about 1.46 million Chinese rows, Apache-2.0 tag, with `question`, `answer`, `name`, `department`, and `id`. The card describes four mixed-origin subsets. It specifically says only the DX subset is authored by doctors and reviewed by doctors; other subsets contain model-authored and model-reviewed data. The current downloader successfully read a sample.
- [FreedomIntelligence/huatuo_knowledge_graph_qa](https://huggingface.co/datasets/FreedomIntelligence/huatuo_knowledge_graph_qa) — 798,444 Chinese rows, Apache-2.0 tag. The card says questions are generated from templates over a medical knowledge graph and answers are graph entries. The downloaded sample uses list-valued `questions` and `answers`, which the source adapter normalizes. Template repetition and the upstream knowledge graph should be considered during cleaning.

Unknown-license candidates, non-commercial-only candidates, inaccessible/gated sources, and datasets whose card or data shape does not fit source collection are left out of this default list. The license shown here is the Hub/card declaration; users should check upstream terms before distributing a derived dataset.
