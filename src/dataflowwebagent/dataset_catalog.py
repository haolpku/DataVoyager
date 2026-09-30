"""Hand-reviewed Hugging Face datasets suitable for the first discovery list.

This is deliberately small: catalog search results are not promoted here until
the dataset card, intended task, schema, license label, and a real sample have
been checked. `card_review` summarizes what to inspect before selecting it.
"""

from __future__ import annotations

import copy


CURATED_DATASETS = {
    "finance": [
        {
            "source": "huggingface",
            "dataset_id": "whpthomas/finqa-parquet",
            "title": "FinQA · 财报数值推理语料",
            "description": "约 8.3k 条英文财报文本与问题，来自约 2.8k 份财务报告。适合从财报证据构建数值型问答；仓库是单个 text 字段，不含标准答案，且把原始 train/dev/test 合并在一起。",
            "tags": ["license:mit", "finance", "question-answering", "english"],
            "language": "English",
            "rows_estimate": 8281,
            "data_kind": "财报证据语料（不是成对 QA 标签）",
            "schema_summary": "text",
            "curator_note": "下载器已验证。数据卡说明 text 由财报前文、问题、后文拼接；发布者注明原始各 split 合并，不能把下载后样本再当独立测试集。",
            "match_score": 10,
            "curated": True,
        },
        {
            "source": "huggingface",
            "dataset_id": "Akhil-Theerthala/PersonalFinance_v2",
            "title": "PersonalFinance v2 · 个人理财场景问答",
            "description": "约 7k 条英文个人理财情景，覆盖债务、退休、投资、税务和预算。包含 query、category、chain_of_thought、response；回答是面向具体个人情况的建议，不是财报事实 QA。",
            "tags": ["license:apache-2.0", "finance", "personal-finance", "question-answering", "english"],
            "language": "English",
            "rows_estimate": 7036,
            "data_kind": "个人理财建议 / 推理 QA",
            "schema_summary": "category, query, chain_of_thought, response",
            "curator_note": "下载器已验证。数据卡说明情景来自 2023 年前的 Reddit 内容，回答经过模型生成和评审；偏美国制度，适合作为个人理财任务候选，不适合作为通用、时效性财务事实来源。",
            "match_score": 9,
            "curated": True,
        },
    ],
    "medical": [
        {
            "source": "huggingface",
            "dataset_id": "Bolin97/MedicalQA",
            "title": "MedicalQA · 中文医疗问答",
            "description": "约 146 万条中文医疗 QA，字段包括 question、answer、name、department；覆盖 31 个科室，适合按疾病和科室筛选后复用已有问答。",
            "tags": ["license:apache-2.0", "medical", "question-answering", "chinese"],
            "language": "中文",
            "rows_estimate": 1464484,
            "data_kind": "中文已有 QA 对",
            "schema_summary": "question, answer, name, department, id",
            "curator_note": "下载器已验证。数据卡将其拆为 DX、HT、TCM、MB 子集：只有 DX 子集明确称医生撰写并由医生复核；其他子集含模型生成或模型审核内容。不能把全库描述成医生审核数据。",
            "match_score": 10,
            "curated": True,
        },
        {
            "source": "huggingface",
            "dataset_id": "FreedomIntelligence/huatuo_knowledge_graph_qa",
            "title": "Huatuo KG QA · 中文医学知识图谱问答",
            "description": "约 798k 条中文医学 QA。问题由医学知识图谱条目套模板生成，答案来自对应知识条目；适合短事实问答，模板重复较多。",
            "tags": ["license:apache-2.0", "medical", "question-answering", "chinese"],
            "language": "中文",
            "rows_estimate": 798444,
            "data_kind": "医学知识图谱模板 QA",
            "schema_summary": "questions (list), answers (list)",
            "curator_note": "数据卡明确记录模板生成方式和上游知识图谱；下载器已验证。接入时要按 questions/answers 的列表字段转换，且合并时注意模板化重复。",
            "match_score": 8,
            "curated": True,
        },
    ],
}


def curated_candidates(domain: str) -> list[dict]:
    """Return copies so callers may safely add request-specific display fields."""
    return copy.deepcopy(CURATED_DATASETS.get(domain, []))
