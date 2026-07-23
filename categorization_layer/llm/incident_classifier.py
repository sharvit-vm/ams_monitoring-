from categorization_layer.llm.prompt_loader import (
    load_prompt
)

from categorization_layer.llm.groq_client import (
    groq_client
)

from categorization_layer.llm.response_parser import (
    parse_response
)

from categorization_layer.utils.logger_config import (
    get_logger
)

logger = get_logger(__name__)


def classify_incident(payload):

    logger.info(
        "AI Classification Started"
    )

    prompt = load_prompt(
        "incident_classifier.txt"
    )

    prompt = prompt.replace(
        "{title}",
        str(payload.get("title", ""))
    )

    prompt = prompt.replace(
        "{description}",
        str(payload.get("description", ""))
    )

    response = groq_client.chat(
        prompt
    )

    result = parse_response(
        response
    )

    logger.info(result)

    return result
