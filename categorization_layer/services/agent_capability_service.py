import json
from pathlib import Path

from categorization_layer.utils.logger_config import get_logger

logger = get_logger(__name__)

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
CONFIG = PACKAGE_ROOT / "config" / "routing_rules.json"

with open(CONFIG, encoding="utf-8") as f:
    ROUTES = json.load(f)


def select_agent(support_level, technology):
    if support_level == "REJECT":
        return {
            "selected_agent": ROUTES["REJECT"]["default"],
            "available": True,
            "backup_agent": None,
        }

    if support_level == "L1":
        return {
            "selected_agent": ROUTES["L1"]["default"],
            "available": True,
            "backup_agent": None,
        }

    selected = ROUTES.get(support_level, {}).get(
        technology,
        ROUTES[support_level]["General"],
    )

    logger.info(
        "[routing] Agent capability matched: "
        f"support_level={support_level}, technology={technology}, selected_agent={selected}"
    )

    return {
        "selected_agent": selected,
        "available": True,
        "backup_agent": "generic_support_agent",
    }
