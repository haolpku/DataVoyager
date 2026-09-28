"""Run one real baseline case; target rows are measured, never assumed achieved.

Set DATAVOYAGER_MODEL, DATAVOYAGER_BASE_URL, DATAVOYAGER_API_KEY and
DATAVOYAGER_API_FORMAT before running. This makes paid API calls.
"""
import argparse
import json
from pathlib import Path
import os
import subprocess
import time

from dataflowwebagent.qa_pipeline import run_qa
from dataflowwebagent.qa_evaluation import audit_items, export_items, structural_metrics, evaluate_variants


def request_for(domain, target):
    if domain == "finance":
        return f"构建 {target} 条面向普通成年读者的中文金融基础知识问答，覆盖债券、共同基金、ETF、利率、风险与收益、分散投资和投资费用。优先参考 Investor.gov、SEC 和 FINRA 的公开投资者教育资料。回答应在2至5句话内解释清楚，所有具体数值与结论必须有采集资料依据，不要给个性化投资建议或收益承诺。英文资料可以用于生成中文问答。"
    return f"构建 {target} 条面向普通成年读者的中文医疗健康教育问答，覆盖高血压、2型糖尿病、症状、危险因素、筛查和一般预防知识。优先参考 MedlinePlus、NIH、NIDDK、NHS 或 WHO 的公开健康教育资料。回答应在2至5句话内解释清楚，所有具体数值与结论必须有采集资料依据，不要给个体诊断、用药剂量或替代医生的治疗建议。英文资料可以用于生成中文问答。"


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--domain", required=True, choices=("finance", "medical"))
    p.add_argument("--target", type=int, required=True)
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--audit", action="store_true")
    p.add_argument("--variants", action="store_true")
    p.add_argument("--judge", default="gpt-6-astra")
    args = p.parse_args()
    if args.target < 1:
        p.error("--target must be positive")
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    request = request_for(args.domain, args.target)
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    meta = {"domain": args.domain, "target_qa": args.target, "page_budget": args.target,
            "request": request, "model": os.environ.get("DATAVOYAGER_MODEL"),
            "baseline": revision, "started_at": time.time()}
    (root / "evaluation-request.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    build_error = None
    try:
        run_qa(request, warehouse=root / "warehouse", run=root / "run", output=root / "qa.jsonl", max_pages=args.target)
    except Exception as exc:
        build_error = str(exc).replace(os.environ.get("DATAVOYAGER_API_KEY") or "\0", "[redacted]")
    items = export_items(root)
    result = structural_metrics(items, args.domain, args.target)
    result["build_error"] = build_error
    if args.audit:
        result["audit"] = audit_items(items, root / "audit", model=args.judge)
    if args.variants:
        result["variants"] = evaluate_variants(root, root / "variants", judge=args.judge)
    (root / "evaluation.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
