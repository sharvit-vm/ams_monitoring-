from categorization_layer.utils.logger_config import get_logger

logger = get_logger(__name__)


REJECT_KEYWORDS = [

    "password reset",

    "forgot password",

    "leave request",

    "vacation",

    "general question",

    "documentation",

    "help",

    "training",

    "test incident",

    "dummy",

    "sample"
]


def validate(payload):

    title = payload.get(
        "title",
        ""
    ).lower()

    description = payload.get(
        "description",
        ""
    ).lower()

    text = title + " " + description

    reasons = []

    for keyword in REJECT_KEYWORDS:

        if keyword in text:

            reasons.append(
                f"Matched reject keyword '{keyword}'"
            )

    if reasons:

        logger.info(
            "Rejected event."
        )

        return {

            "valid": False,

            "confidence": 0.99,

            "reason": "Rejected incident",

            "reasoning": reasons
        }

    return {

        "valid": True,

        "confidence": 0.95,

        "reason": "Valid operational incident",

        "reasoning": [
            "No reject rules matched."
        ]
    }
