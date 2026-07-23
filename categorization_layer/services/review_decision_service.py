import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]

with open(PACKAGE_ROOT / "config" / "review_policy.json", encoding="utf-8") as file:
    POLICY = json.load(file)


def should_review(rule_result, ai_result, final_result):
    if final_result["confidence"] < POLICY["min_confidence"]:
        return True

    if POLICY["review_on_ai_disagreement"]:
        if rule_result["support_level"] != ai_result["support_level"]:
            return True

    if POLICY["review_on_unknown_technology"] and final_result["technology"] == "General":
        return True

    return False
