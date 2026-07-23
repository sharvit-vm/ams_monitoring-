from categorization_layer.utils.logger_config import get_logger

logger = get_logger(__name__)


def generate_reasoning(

        validation,

        support_level,

        technology,

        business_impact,

        matched_rules

):

    reasoning = []

    reasoning.extend(

        validation[
            "reasoning"
        ]
    )

    reasoning.append(

        f"Support Level : {support_level}"
    )

    reasoning.append(

        f"Technology : {technology}"
    )

    reasoning.append(

        f"Business Impact : {business_impact}"
    )

    if matched_rules:

        reasoning.append(

            "Matched Rules : "

            +

            ", ".join(
                matched_rules
            )
        )

    logger.info(
        "Generated explanation."
    )

    return reasoning
