from categorization_layer.utils.logger_config import (
    get_logger
)

logger = get_logger(__name__)


# ---------------------------------------------------
# Operational Risk Assessment
# ---------------------------------------------------
def evaluate_risk(

        payload,

        technology,

        business_criticality

):

    score = 0

    reasons = []

    priority = payload.get(
        "priority"
    )

    severity = str(

        payload.get(
            "severity",
            ""
        )

    ).lower()

    # -----------------------------------
    # Priority
    # -----------------------------------

    PRIORITY_WEIGHT = {

        "P1": 40,

        "P2": 30,

        "P3": 20,

        "P4": 10,

        "P5": 5
    }

    score += PRIORITY_WEIGHT.get(

        priority,

        0

    )

    reasons.append(

        f"Priority {priority}"

    )

    # -----------------------------------
    # Severity
    # -----------------------------------

    SEVERITY_WEIGHT = {

        "critical": 30,

        "high": 20,

        "medium": 10,

        "low": 5
    }

    score += SEVERITY_WEIGHT.get(

        severity,

        0

    )

    reasons.append(

        f"Severity {severity}"

    )

    # -----------------------------------
    # Technology
    # -----------------------------------

    HIGH_RISK_TECH = [

        "Infrastructure",

        "Database",

        "Cloud",

        "Kubernetes"
    ]

    if technology in HIGH_RISK_TECH:

        score += 20

        reasons.append(

            f"{technology} Platform"

        )

    # -----------------------------------
    # Business Criticality
    # -----------------------------------

    if business_criticality == "CRITICAL":

        score += 20

        reasons.append(

            "Critical Business Impact"

        )

    elif business_criticality == "HIGH":

        score += 10

        reasons.append(

            "High Business Impact"

        )

    # -----------------------------------
    # Risk Level
    # -----------------------------------

    if score >= 90:

        risk = "CRITICAL"

    elif score >= 70:

        risk = "HIGH"

    elif score >= 40:

        risk = "MEDIUM"

    else:

        risk = "LOW"

    logger.info(

        f"Risk : {risk}"

    )

    return {

        "risk": risk,

        "risk_score": score,

        "reasoning": reasons
    }
