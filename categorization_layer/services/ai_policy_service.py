import json
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PACKAGE_ROOT / "config" / "ai_policy.json"


def load_ai_policy():
    with open(CONFIG, encoding="utf-8") as file:
        return json.load(file)


policy = load_ai_policy()
