from categorization_layer.utils.logger_config import get_logger

logger = get_logger(__name__)


def calculate_confidence_breakdown(

        validation_confidence,

        support_score,

        technology_found,

        business_impact

):

    confidence = validation_confidence
    steps = [
        {
            "signal": "base_validation_confidence",
            "value": validation_confidence,
            "running_total": confidence
        }
    ]

    # -------------------------
    # Positive Signals
    # -------------------------

    if support_score >= 3:

        confidence += 0.20
        steps.append({
            "signal": "support_score_high",
            "condition": "support_score >= 3",
            "delta": 0.20,
            "running_total": confidence
        })

    elif support_score == 2:

        confidence += 0.10
        steps.append({
            "signal": "support_score_medium",
            "condition": "support_score == 2",
            "delta": 0.10,
            "running_total": confidence
        })

    elif support_score == 1:

        confidence += 0.05
        steps.append({
            "signal": "support_score_low",
            "condition": "support_score == 1",
            "delta": 0.05,
            "running_total": confidence
        })

    if technology_found:

        confidence += 0.10
        steps.append({
            "signal": "technology_detected",
            "condition": "technology_found is true",
            "delta": 0.10,
            "running_total": confidence
        })

    if business_impact == "HIGH":

        confidence += 0.05
        steps.append({
            "signal": "business_impact_high",
            "condition": "business_impact == HIGH",
            "delta": 0.05,
            "running_total": confidence
        })

    elif business_impact == "CRITICAL":

        confidence += 0.10
        steps.append({
            "signal": "business_impact_critical",
            "condition": "business_impact == CRITICAL",
            "delta": 0.10,
            "running_total": confidence
        })

    # -------------------------
    # Negative Signals
    # -------------------------

    if not technology_found:

        confidence -= 0.20
        steps.append({
            "signal": "technology_not_detected",
            "condition": "technology_found is false",
            "delta": -0.20,
            "running_total": confidence
        })

    if business_impact == "UNKNOWN":

        confidence -= 0.15
        steps.append({
            "signal": "business_impact_unknown",
            "condition": "business_impact == UNKNOWN",
            "delta": -0.15,
            "running_total": confidence
        })

    if support_score == 0:

        confidence -= 0.20
        steps.append({
            "signal": "no_support_rule_match",
            "condition": "support_score == 0",
            "delta": -0.20,
            "running_total": confidence
        })

    # -------------------------
    # Clamp
    # -------------------------

    unclamped_confidence = confidence
    confidence = max(0.0, min(confidence, 1.0))
    rounded_confidence = round(confidence, 2)
    if confidence != unclamped_confidence:
        steps.append({
            "signal": "clamp_to_valid_range",
            "condition": "confidence must be between 0.0 and 1.0",
            "before": unclamped_confidence,
            "after": confidence,
            "running_total": confidence
        })

    logger.info(
        f"Confidence : {rounded_confidence}"
    )

    return {
        "confidence": rounded_confidence,
        "base": validation_confidence,
        "support_score": support_score,
        "technology_found": technology_found,
        "business_impact": business_impact,
        "unclamped": round(unclamped_confidence, 4),
        "final": rounded_confidence,
        "steps": steps
    }


def calculate_confidence(

        validation_confidence,

        support_score,

        technology_found,

        business_impact

):

    return calculate_confidence_breakdown(
        validation_confidence,
        support_score,
        technology_found,
        business_impact
    )["confidence"]

