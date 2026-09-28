import json
import pytest

from dataflowwebagent.qa_evaluation import apply_format_rules, audit_items, structural_metrics, validate_judgment


def judgment(**changes):
    return {"grounded": True, "on_topic": True, "format_ok": True, "educational_safe": True,
            "evidence_quotes": ["Bonds carry interest-rate risk."], "issues": [], **changes}


def test_quote_matching_does_not_accept_fabricated_support():
    assert validate_judgment(judgment(), "Bonds carry  interest-rate risk.")["passed"]
    assert not validate_judgment(judgment(), "Stocks can pay dividends.")["passed"]
    assert not validate_judgment(judgment(evidence_quotes=[]), "Bonds carry interest-rate risk.")["passed"]


def test_string_boolean_cannot_be_counted_as_a_pass():
    with pytest.raises(ValueError, match="boolean"):
        validate_judgment(judgment(grounded="false"), "Bonds carry interest-rate risk.")


def test_objective_format_rules_override_sentence_counting_errors():
    row = {"request": "中文科普，回答只用2到3句话", "qa": {"instruction": "如何规划？", "output": "先明确目标。此外，核查背景。"}}
    result = apply_format_rules(row, {**judgment(format_ok=False), "evidence_quotes_match": True})
    assert result["passed"] and result["format_rule_results"]["sentence_count"] == 2
    row["qa"]["output"] = "只有一句话。"
    assert not apply_format_rules(row, {**judgment(), "evidence_quotes_match": True})["passed"]


def test_quantity_and_preferred_sources_are_not_faked_by_page_budget():
    items = [{"qa": {"instruction": "What is a bond?", "output": "A debt instrument."},
              "source_url": url, "source_text": "A bond is a debt instrument."}
             for url in ("https://www.investor.gov/bonds", "https://investor.gov.evil.com/bonds")]
    result = structural_metrics(items, "finance", 1000)
    assert result["exported_rows"] == 2
    assert result["target_attainment"] == .002
    assert result["duplicate_questions"] == 1
    assert result["preferred_source_rows"] == 1
    assert structural_metrics([], "finance", 100)["target_attainment"] == 0


def test_audit_errors_are_not_passes_and_usage_is_kept(tmp_path, monkeypatch):
    from dataflowwebagent.agents.Obtainer.datamixer import llm
    monkeypatch.setenv("DATAVOYAGER_BASE_URL", "https://model.invalid/v1")
    monkeypatch.setenv("DATAVOYAGER_API_KEY", "private-key")

    def post(url, payload, key, timeout):
        assert key == "private-key"
        assert "temperature" not in payload and "top_p" not in payload
        row = json.loads(payload["input"][-1]["content"])["row"]
        answer = judgment() if row == 1 else judgment(grounded="false")
        return {"output_text": json.dumps(answer), "usage": {"input_tokens": 10, "output_tokens": 5}}

    monkeypatch.setattr(llm, "_post", post)
    items = [{"row": i, "source_text": "Bonds carry interest-rate risk."} for i in (1, 2)]
    report = audit_items(items, tmp_path / "audit", samples=2)
    assert report["audited"] == 2 and report["passed"] == 1 and report["audit_errors"] == 1
    assert report["usage"]["calls"] == 2 and report["usage"]["total_tokens"] == 30
    assert "private-key" not in (tmp_path / "audit" / "judgments.jsonl").read_text()
