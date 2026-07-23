from categorization_layer.utils.logger_config import get_logger

logger = get_logger(__name__)


def requires_human_review(

        confidence,

        business_criticality,

        risk,

        duplicate,

        support_level

):

    reasons = []

    review = False

    # ---------------------------------------
    # Low Confidence
    # ---------------------------------------

    if confidence < 0.70:

        review = True

        reasons.append(
            "Low confidence score."
        )

    # ---------------------------------------
    # Critical Business Impact
    # ---------------------------------------

    if business_criticality == "CRITICAL":

        review = True

        reasons.append(
            "Critical business service."
        )

    # ---------------------------------------
    # High Risk
    # ---------------------------------------

    if risk == "CRITICAL":

        review = True

        reasons.append(
            "Critical operational risk."
        )

    # ---------------------------------------
    # Duplicate Incident
    # ---------------------------------------

    if duplicate:

        review = True

        reasons.append(
            "Possible duplicate incident."
        )

    # L3 diagnosis can run automatically. Human approval should be enforced
    # later by remediation policy when a risky code/config change is proposed.

    logger.info(

        f"Human Review : {review}"

    )

    return {

        "requires_review": review,

        "reasoning": reasons
    }

