from categorization_layer.utils.logger_config import (
    get_logger
)

logger = get_logger(__name__)


# ---------------------------------------------------
# Business Criticality Evaluation
# ---------------------------------------------------
def evaluate_business_criticality(payload):

    score = 0

    reasons = []

    text = (

        str(payload.get(
            "title", ""
        ))

        + " "

        + str(payload.get(
            "description", ""
        ))

    ).lower()

    environment = str(

        payload.get(
            "environment",
            ""
        )

    ).lower()

    service = str(

        payload.get(
            "business_service",
            ""
        )

    ).lower()

    priority = str(

        payload.get(
            "priority",
            ""
        )

    )

    # -----------------------------------
    # Environment
    # -----------------------------------

    if environment == "production":

        score += 30

        reasons.append(
            "Production Environment"
        )

    elif environment == "uat":

        score += 15

        reasons.append(
            "UAT Environment"
        )

    elif environment == "dev":

        score += 5

        reasons.append(
            "Development Environment"
        )

    # -----------------------------------
    # Priority
    # -----------------------------------

    if priority == "P1":

        score += 30

        reasons.append(
            "Priority P1"
        )

    elif priority == "P2":

        score += 20

        reasons.append(
            "Priority P2"
        )

    elif priority == "P3":

        score += 10

        reasons.append(
            "Priority P3"
        )

    # -----------------------------------
    # Business Service
    # -----------------------------------

    CRITICAL_SERVICES = [

        "payments",

        "claims",

        "policy",

        "billing",

        "authentication",

        "identity"
    ]

    for service_name in CRITICAL_SERVICES:

        if service_name in service:

            score += 25

            reasons.append(

                f"Critical Business Service: {service_name}"

            )

            break

    # -----------------------------------
    # Customer Impact
    # -----------------------------------

    CUSTOMER_KEYWORDS = [

        "customer",

        "client",

        "external user",

        "policy holder"
    ]

    for keyword in CUSTOMER_KEYWORDS:

        if keyword in text:

            score += 20

            reasons.append(

                "Customer Facing Impact"

            )

            break

    # -----------------------------------
    # Determine Criticality
    # -----------------------------------

    if score >= 80:

        criticality = "CRITICAL"

    elif score >= 60:

        criticality = "HIGH"

    elif score >= 40:

        criticality = "MEDIUM"

    else:

        criticality = "LOW"

    logger.info(

        f"Business Criticality : {criticality}"

    )

    return {

        "criticality": criticality,

        "score": score,

        "reasoning": reasons
    }
