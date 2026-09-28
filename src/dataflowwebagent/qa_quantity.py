"""Explicit QA targets and honest shortfall states, separate from crawl budgets."""
from __future__ import annotations

import re


def infer_target(request: str) -> int | None:
    """Conservative fallback for explicit counts; the chat controller handles prose."""
    numbers = r"([0-9][0-9,]*|[一二两三四五六七八九十百千万]+)"
    patterns = [numbers + r"\s*(?:条|道|个)\s*[\u4e00-\u9fffA-Za-z]{0,32}(?:QA|问答|问题|题目|题|样本|数据)",
                numbers + r"\s*(?:题|问答)",
                numbers + r"\s*(?:QA(?:\s+pairs?)?|questions?|question-answer\s+pairs?|examples?)\b"]
    found = []
    for pattern in patterns:
        for match in re.finditer(pattern, request, re.I):
            value = match.group(1).replace(",", "")
            if value.isdigit():
                count = int(value)
            else:
                digits = dict(zip("一二两三四五六七八九", (1, 2, 2, 3, 4, 5, 6, 7, 8, 9)))
                count = section = digit = 0
                for char in value:
                    if char in digits:
                        digit = digits[char]
                    elif char == "万":
                        count += (section + digit or 1) * 10000
                        section = digit = 0
                    else:
                        section += (digit or 1) * {"十": 10, "百": 100, "千": 1000}[char]
                        digit = 0
                count += section + digit
            found.append(count)
    unique = set(found)
    return unique.pop() if len(unique) == 1 else None


def resolve_target(request: str, target: int | None = None) -> int | None:
    if target is not None and type(target) is not int:
        raise ValueError("QA target must be 1–10000")
    target = infer_target(request) if target is None or target == 0 else target
    if target is not None and (type(target) is not int or not 1 <= target <= 10000):
        raise ValueError("QA target must be 1–10000")
    return target


def quantity_result(rows: int, target: int | None, reason: str = "") -> dict:
    met = target is None or rows >= target
    return {"status": "completed" if met else "needs_confirmation", "target_rows": target,
            "target_met": rows >= target if target else None,
            "shortfall": max(0, target - rows) if target else 0,
            "stop_reason": "target_reached" if target and met else reason,
            "quantity_basis": "structurally_valid_unique_questions; factual_verification_not_performed"}
