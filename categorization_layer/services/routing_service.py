from categorization_layer.services.agent_capability_service import (
    select_agent
)

from categorization_layer.utils.logger_config import (
    get_logger
)

logger = get_logger(__name__)


def determine_route(

        support_level,

        technology

):

    logger.info(
        "[routing] Determining RCA route."
    )

    agent = select_agent(

        support_level,

        technology

    )

    logger.info(
        "[routing] Selected RCA route: "
        f"support_level={support_level}, technology={technology}, "
        f"selected_agent={agent['selected_agent']}, backup_agent={agent['backup_agent']}"
    )

    return agent

