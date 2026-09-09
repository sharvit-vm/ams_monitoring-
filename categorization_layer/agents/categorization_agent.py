import os

from categorization_layer.models.categorization_result import (
    CategorizationResult
)

from categorization_layer.utils.logger_config import (
    get_logger
)
from categorization_layer.llm.incident_validator import (
    validate_incident
)

from categorization_layer.services.ai_policy_service import (
    policy
)

# -----------------------------
# Rules
# -----------------------------
from categorization_layer.rules.validation_rules import (
    validate
)

from categorization_layer.rules.l1_rules import (
    match as l1_match
)

from categorization_layer.rules.l2_rules import (
    match as l2_match
)

from categorization_layer.rules.l3_rules import (
    match as l3_match
)

from categorization_layer.rules.technology_rules import (
    detect
)

from categorization_layer.rules.business_rules import (
    evaluate as evaluate_business
)

from categorization_layer.rules.priority_rules import (
    evaluate as evaluate_priority
)

# -----------------------------
# Services
# -----------------------------
from categorization_layer.services.business_criticality_service import (
    evaluate_business_criticality
)

from categorization_layer.services.risk_service import (
    evaluate_risk
)

from categorization_layer.services.duplicate_service import (
    check_duplicate
)

from categorization_layer.services.confidence_service import (
    calculate_confidence_breakdown
)

from categorization_layer.services.human_review_service import (
    requires_human_review
)

from categorization_layer.services.routing_service import (
    determine_route
)

from categorization_layer.services.explanation_service import (
    generate_reasoning
)
from categorization_layer.llm.incident_classifier import (
    classify_incident
)

from categorization_layer.services.decision_merger import (
    merge_decision
)
from observability.token_usage import track_usage

logger = get_logger(__name__)
AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW = (
    os.getenv("AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW", "false").lower()
    in ("1", "true", "yes")
)


STAGE_LABELS = {
    "request.normalized_input": "STEP 01 - Normalized payload received",
    "validation.completed": "STEP 02 - Incident validation completed",
    "decision.rejected": "STEP 03 - Incident rejected",
    "support_rules.evaluated": "STEP 03 - L1/L2/L3 rule evidence evaluated",
    "support_level.selected": "STEP 04 - RCA level selected",
    "technology.detected": "STEP 05 - Technology/domain detection completed",
    "business_impact.evaluated": "STEP 06 - Business impact evaluated",
    "priority.evaluated": "STEP 07 - Priority evaluated",
    "business_criticality.evaluated": "STEP 08 - Business criticality evaluated",
    "risk.evaluated": "STEP 09 - Operational risk evaluated",
    "duplicate.checked": "STEP 10 - Duplicate check completed",
    "confidence.calculated": "STEP 11 - Confidence calculated",
    "ai_validation.completed": "STEP 12 - Agentic validation completed",
    "ai_classification.completed": "STEP 13 - Agentic classification completed",
    "decision.merged_with_ai": "STEP 14 - Deterministic and agentic decisions merged",
    "human_review.evaluated": "STEP 15 - Human review decision completed",
    "routing.selected": "STEP 16 - RCA route selected",
    "reasoning.generated": "STEP 17 - Explanation generated",
    "decision.final": "STEP 18 - Final categorization decision"
}


def _display_name(value):

    return str(value).replace("_", " ").title()


def _format_value(value):

    if value is None:
        return "None"

    if isinstance(value, bool):
        return "Yes" if value else "No"

    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "None"

    if isinstance(value, dict):
        parts = []
        for key, item in value.items():
            parts.append(f"{key}={_format_value(item)}")
        return "; ".join(parts) if parts else "None"

    return str(value)


def _log_stage(stage, **fields):

    label = STAGE_LABELS.get(stage, stage)
    logger.info(f"[categorization] {label}")

    for key, value in fields.items():
        if key == "calculation" and isinstance(value, dict):
            _log_confidence_calculation(value)
            continue
        if key == "evidence" and isinstance(value, dict):
            logger.info("[categorization]   Evidence:")
            for evidence_key, evidence_value in value.items():
                logger.info(
                    f"[categorization]     - {_display_name(evidence_key)}: {_format_value(evidence_value)}"
                )
            continue
        logger.info(
            f"[categorization]   {_display_name(key)}: {_format_value(value)}"
        )


def _log_confidence_calculation(details):

    logger.info(
        f"[categorization]   Confidence Formula: start with validation confidence {details.get('base')}"
    )
    for step in details.get("steps", []):
        signal = step.get("signal")
        if signal == "base_validation_confidence":
            logger.info(
                f"[categorization]     - Base: {step.get('value')} from validation result"
            )
            continue
        delta = step.get("delta")
        if delta is None:
            logger.info(
                f"[categorization]     - {step.get('condition', signal)}; running total = {round(step.get('running_total', 0), 4)}"
            )
            continue
        sign = "+" if isinstance(delta, (int, float)) and delta >= 0 else ""
        condition = step.get("condition", signal)
        logger.info(
            f"[categorization]     - {sign}{delta}: {condition}; running total = {round(step.get('running_total', 0), 4)}"
        )
    logger.info(
        f"[categorization]   Final Confidence: {details.get('final')} (before clamp: {details.get('unclamped')})"
    )


def _payload_summary(payload):

    description = payload.get("description") or ""

    return {
        "ticket_id": payload.get("ticket_id"),
        "normalised_event_id": payload.get("normalised_event_id"),
        "source_event_id": payload.get("source_event_id"),
        "source": payload.get("source"),
        "incident_id": payload.get("incident_id"),
        "external_id": payload.get("external_id"),
        "title": payload.get("title"),
        "priority": payload.get("priority"),
        "environment": payload.get("environment"),
        "business_service": payload.get("business_service"),
        "repo_full_name": payload.get("repo_full_name"),
        "error_type": payload.get("error_type"),
        "file_path": payload.get("file_path"),
        "function_name": payload.get("function_name"),
        "line_number": payload.get("line_number"),
        "has_traceback": bool(payload.get("traceback")),
        "is_code_issue": payload.get("is_code_issue"),
        "description_chars": len(description)
    }


def _code_evidence(payload):

    evidence = []

    if payload.get("traceback"):
        evidence.append("traceback")

    if payload.get("file_path"):
        evidence.append(f"file_path={payload.get('file_path')}")

    if payload.get("function_name"):
        evidence.append(f"function_name={payload.get('function_name')}")

    if payload.get("line_number"):
        evidence.append(f"line_number={payload.get('line_number')}")

    if payload.get("is_code_issue") is True:
        evidence.append("is_code_issue=true")

    return evidence


def _first_text(*values):

    for value in values:

        if value is not None and str(value).strip():

            return str(value).strip()

    return ""


def _normalise_priority(priority):

    mapping = {

        "1": "P1",

        "2": "P2",

        "3": "P3",

        "4": "P4",

        "5": "P5",

        "p1": "P1",

        "p2": "P2",

        "p3": "P3",

        "p4": "P4",

        "p5": "P5"
    }

    value = str(priority or "").strip()

    return mapping.get(value.lower(), value)


def _join_text(*values):

    parts = []

    for value in values:

        if isinstance(value, list):

            text = " ".join(str(item) for item in value if item)

        else:

            text = str(value or "")

        if text.strip():

            parts.append(text.strip())

    return "\n".join(parts)


def adapt_normalized_event(payload):

    """
    Accept both the old ticket payload and the AMS normalised ErrorEvent.

    Existing rules operate on ticket_id/title/description. This adapter fills
    those fields from incident_id, short_description, message, traceback and
    other normalised fields while preserving the original event.
    """

    adapted = dict(payload)

    ticket_id = _first_text(

        adapted.get("ticket_id"),

        adapted.get("incident_id"),

        adapted.get("external_id"),

        adapted.get("source_event_id"),

        adapted.get("id")
    )

    title = _first_text(

        adapted.get("title"),

        adapted.get("short_description"),

        adapted.get("message"),

        adapted.get("error_type")
    )

    labels = adapted.get("labels") or []

    components = adapted.get("components") or []

    description = _join_text(

        adapted.get("description"),

        adapted.get("raw_description"),

        adapted.get("traceback"),

        adapted.get("message"),

        adapted.get("error_type"),

        adapted.get("file_path"),

        adapted.get("function_name"),

        labels,

        components
    )

    adapted["ticket_id"] = ticket_id or "unknown"

    adapted["title"] = title

    adapted["description"] = description

    adapted["priority"] = _normalise_priority(adapted.get("priority"))

    adapted["environment"] = _first_text(

        adapted.get("environment"),

        adapted.get("impacted_environment"),

        "production"
    )

    adapted["business_service"] = _first_text(

        adapted.get("business_service"),

        adapted.get("configuration_item"),

        adapted.get("assignment_group"),

        adapted.get("repo_full_name")
    )

    adapted["normalised_event_id"] = adapted.get("id")

    adapted["raw_normalised_event"] = payload

    return adapted


def _category_for(support, technology, payload):

    if support == "L3":

        return "code"

    if support == "L2":

        return technology.lower() if technology else "operations"

    if support == "L1":

        return "support"

    if support == "NONE":

        return "rejected"

    return payload.get("issue_category") or "unknown"


def _recommended_action(support, reject, requires_human):

    if reject:

        return "reject"

    if requires_human:

        return "human_review"

    mapping = {

        "L1": "run_l1_rca",

        "L2": "run_l2_rca",

        "L3": "run_l3_rca"
    }

    return mapping.get(support, "human_review")


def _has_code_evidence(payload):

    return bool(

        payload.get("traceback")

        or payload.get("file_path")

        or payload.get("function_name")

        or payload.get("line_number")

        or payload.get("is_code_issue") is True
    )


@track_usage
def categorize(payload):

    logger.info("=" * 60)
    logger.info("CATEGORIZATION STARTED")
    logger.info("=" * 60)
    decision_source = "deterministic"

    payload = adapt_normalized_event(payload)
    _log_stage(
        "request.normalized_input",
        payload=_payload_summary(payload)
    )

    # ======================================================
    # Validation
    # ======================================================

    validation = validate(payload)
    _log_stage(
        "validation.completed",
        valid=validation["valid"],
        confidence=validation["confidence"],
        reason=validation["reason"],
        reasoning=validation["reasoning"]
    )

    if not validation["valid"]:

       logger.info("Validation Failed")
       _log_stage(
           "decision.rejected",
           reason=validation["reason"],
           confidence=validation["confidence"],
           evidence=validation["reasoning"]
       )

       return CategorizationResult(

        ticket_id=payload.get("ticket_id"),

        classification="REJECT",

        support_level="NONE",

        technology="General",

        team="",

        priority="",

        business_impact="LOW",

        business_criticality="LOW",

        risk="LOW",

        risk_score=0,

        duplicate=False,

        duplicate_of=None,

        confidence=validation["confidence"],

        requires_human=False,

        selected_agent="reject_agent",

        backup_agent=None,

        available=True,

        reject=True,

        route_to="reject_agent",

        reason=validation["reason"],

        reasoning=validation["reasoning"],

        is_valid_incident=False,

        category="rejected",

        rca_level="reject",

        needs_human_review=False,

        recommended_next_action="reject",

        source_event_id=payload.get("source_event_id"),

        normalised_event_id=payload.get("normalised_event_id")
    )

    # ======================================================
    # L1 / L2 / L3 Classification
    # ======================================================

    l1_score, l1_rules = l1_match(
        payload
    )

    l2_score, l2_rules = l2_match(
        payload
    )

    l3_score, l3_rules = l3_match(
        payload
    )

    code_evidence = _code_evidence(payload)
    if _has_code_evidence(payload):

        l3_score += 3

        l3_rules = list(l3_rules)

        l3_rules.append(
            "normalised code evidence"
        )

    _log_stage(
        "support_rules.evaluated",
        scores={
            "L1": l1_score,
            "L2": l2_score,
            "L3": l3_score
        },
        matched_rules={
            "L1": l1_rules,
            "L2": l2_rules,
            "L3": l3_rules
        },
        code_evidence=code_evidence,
        code_evidence_bonus=3 if code_evidence else 0
    )

    if l1_score >= l2_score and l1_score >= l3_score:

        support = "L1"

        matched_rules = l1_rules

    elif l2_score >= l3_score:

        support = "L2"

        matched_rules = l2_rules

    else:

        support = "L3"

        matched_rules = l3_rules

    logger.info(
        f"Support Level : {support}"
    )
    _log_stage(
        "support_level.selected",
        support_level=support,
        selected_score=max(l1_score, l2_score, l3_score),
        selected_evidence=matched_rules,
        route_basis=(
            f"{support} had the highest score. "
            f"Scores were L1={l1_score}, L2={l2_score}, L3={l3_score}."
        )
    )

    # ======================================================
    # Technology Detection
    # ======================================================

    technology, _ = detect(
        payload
    )

    logger.info(
        f"Technology : {technology}"
    )
    _log_stage(
        "technology.detected",
        technology=technology,
        technology_found=technology != "General"
    )

    # ======================================================
    # Business Impact
    # ======================================================

    business_impact = evaluate_business(
        payload
    )

    logger.info(
        f"Business Impact : {business_impact}"
    )
    _log_stage(
        "business_impact.evaluated",
        business_impact=business_impact
    )

    # ======================================================
    # Priority
    # ======================================================

    priority = evaluate_priority(
        payload
    )

    logger.info(
        f"Priority : {priority}"
    )
    _log_stage(
        "priority.evaluated",
        priority=priority
    )

    # ======================================================
    # Business Criticality
    # ======================================================

    criticality = evaluate_business_criticality(
        payload
    )

    logger.info(
        f"Criticality : {criticality['criticality']}"
    )
    _log_stage(
        "business_criticality.evaluated",
        criticality=criticality["criticality"],
        score=criticality.get("score"),
        evidence=criticality.get("reasoning", [])
    )

    # ======================================================
    # Risk Assessment
    # ======================================================

    risk = evaluate_risk(

        payload,

        technology,

        criticality[
            "criticality"
        ]
    )

    logger.info(
        f"Risk : {risk['risk']}"
    )
    _log_stage(
        "risk.evaluated",
        risk=risk["risk"],
        risk_score=risk["risk_score"],
        evidence=risk.get("reasoning", [])
    )

    # ======================================================
    # Duplicate Detection
    # ======================================================

    duplicate = check_duplicate({

        "ticket_id":

            payload.get(
                "ticket_id"
            ),

        "title":

            payload.get(
                "title"
            ),

        "technology":

            technology
    })

    logger.info(
        f"Duplicate : {duplicate['duplicate']}"
    )
    _log_stage(
        "duplicate.checked",
        duplicate=duplicate["duplicate"],
        duplicate_of=duplicate.get("duplicate_of"),
        reason=duplicate.get("reason")
    )

    # ======================================================
    # Confidence
    # ======================================================

    confidence_details = calculate_confidence_breakdown(

        validation[
            "confidence"
        ],

        max(

            l1_score,

            l2_score,

            l3_score
        ),

        technology != "General",

        business_impact
    )
    confidence = confidence_details["confidence"]

    logger.info(
        f"Confidence : {confidence}"
    )
    _log_stage(
        "confidence.calculated",
        confidence=confidence,
        calculation=confidence_details,
        evidence={
            "validation_confidence": validation["confidence"],
            "support_level": support,
            "support_score": max(l1_score, l2_score, l3_score),
            "matched_rules": matched_rules,
            "technology": technology,
            "business_impact": business_impact,
            "code_evidence": code_evidence
        }
    )
    
    # ======================================================
    # AI Validation + AI Classification
    # ======================================================

    if policy["enable_ai_validation"]:

       if confidence < policy["confidence_threshold"]:

        logger.info("=" * 60)
        logger.info("LOW CONFIDENCE - INVOKING AI")
        logger.info("=" * 60)
        decision_source = "agentic_validation"

        # -----------------------------
        # AI Validation
        # -----------------------------
        logger.info("Calling AI Validator...")
        try:

            validation_result = validate_incident(
                payload
            )

        except Exception as exc:

            logger.error(
                f"AI validation failed; falling back to human review: {exc}"
            )

            confidence = min(
                confidence,
                0.0
            )

            validation_result = {
                "is_incident": True,
                "confidence": confidence,
                "reason": "AI validation unavailable; rule-based categorisation requires human review.",
                "ai_unavailable": True
            }

        _log_stage(
            "ai_validation.completed",
            result=validation_result
        )

        if not validation_result["is_incident"]:

            logger.info("Rejected by AI Validator")

            return CategorizationResult(

                ticket_id=payload.get("ticket_id"),

                classification="REJECT",

                support_level="NONE",

                technology="General",

                team="",

                priority="",

                business_impact="LOW",

                business_criticality="LOW",

                risk="LOW",

                risk_score=0,

                duplicate=False,

                duplicate_of=None,

                confidence=validation_result["confidence"],

                requires_human=False,

                selected_agent="reject_agent",

                backup_agent=None,

                available=True,

                reject=True,

                route_to="reject_agent",

                reason=validation_result["reason"],

                reasoning=[
                    "Rejected by AI Validator."
                ],

                is_valid_incident=False,

                category="rejected",

                rca_level="reject",

                needs_human_review=False,

                recommended_next_action="reject",

                source_event_id=payload.get("source_event_id"),

                normalised_event_id=payload.get("normalised_event_id")
            )

        if validation_result.get("ai_unavailable"):

            logger.info("Skipping AI classifier because AI validation is unavailable.")

        else:

            logger.info("AI confirmed valid incident.")
            logger.info("Calling AI Classifier...")
            # -----------------------------
            # AI Classification
            # -----------------------------

            ai_result = classify_incident(
                payload
            )

            _log_stage(
                "ai_classification.completed",
                result=ai_result
            )

            rule_result = {

               "support_level": support,

               "technology": technology,

                "business_impact": business_impact,

                "priority": priority,

                "criticality": criticality["criticality"],

                "risk": risk["risk"],

                "confidence": confidence
           }

            merged = merge_decision(

                rule_result,

                ai_result

            )
            decision_source = "agentic_ai_assisted"

            support = merged["support_level"]

            technology = merged["technology"]

            business_impact = merged["business_impact"]

            confidence = merged["confidence"]
            _log_stage(
                "decision.merged_with_ai",
                rule_result=rule_result,
                ai_result=ai_result,
                merged=merged
            )
    # ======================================================
    # Human Review
    # ======================================================

    review = requires_human_review(

        confidence,

        criticality[
            "criticality"
        ],

        risk[
            "risk"
        ],

        duplicate[
            "duplicate"
        ],

        support
    )

    if support == "L2" and AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW:
        original_reasons = review.get("reasoning", [])
        review = {
            "requires_review": False,
            "reasoning": original_reasons
            + ["L2 auto-routing override enabled for RCA delegation."]
        }

    logger.info(
        f"Human Review : {review['requires_review']}"
    )
    _log_stage(
        "human_review.evaluated",
        requires_review=review["requires_review"],
        evidence=review.get("reasoning", [])
    )

    # ======================================================
    # Routing
    # ======================================================

    route = determine_route(

        support,

        technology
    )

    selected_agent = route[
        "selected_agent"
    ]

    backup_agent = route[
        "backup_agent"
    ]

    available = route[
        "available"
    ]

    logger.info(
        f"Selected Agent : {selected_agent}"
    )
    _log_stage(
        "routing.selected",
        support_level=support,
        technology=technology,
        selected_agent=selected_agent,
        backup_agent=backup_agent,
        available=available
    )

    # ======================================================
    # Explanation
    # ======================================================

    reasoning = generate_reasoning(

        validation,

        support,

        technology,

        business_impact,

        matched_rules
    )
    _log_stage(
        "reasoning.generated",
        reasoning=reasoning
    )

    logger.info("=" * 60)
    logger.info("CATEGORIZATION COMPLETED")
    logger.info("=" * 60)

    recommended_next_action = _recommended_action(

        support,

        False,

        review[
            "requires_review"
        ]
    )
    _log_stage(
        "decision.final",
        classification="INCIDENT",
        decision_source=decision_source,
        support_level=support,
        rca_level=support,
        category=_category_for(
            support,
            technology,
            payload
        ),
        technology=technology,
        confidence=confidence,
        requires_human_review=review["requires_review"],
        selected_agent=selected_agent,
        recommended_next_action=recommended_next_action,
        evidence={
            "matched_rules": matched_rules,
            "code_evidence": code_evidence,
            "risk": risk,
            "criticality": criticality,
            "duplicate": duplicate
        }
    )

    return CategorizationResult(

        ticket_id=payload.get(
            "ticket_id"
        ),

        classification="INCIDENT",

        support_level=support,

        technology=technology,

        team=technology,

        priority=priority,

        business_impact=business_impact,

        business_criticality=criticality[
            "criticality"
        ],

        risk=risk[
            "risk"
        ],

        risk_score=risk[
            "risk_score"
        ],

        duplicate=duplicate[
            "duplicate"
        ],

        duplicate_of=duplicate[
            "duplicate_of"
        ],

        confidence=confidence,

        requires_human=review[
            "requires_review"
        ],

        selected_agent=selected_agent,

        backup_agent=backup_agent,

        available=available,

        reject=False,

        route_to=selected_agent,

        reason="Incident Categorized",

        reasoning=reasoning,

        is_valid_incident=True,

        category=_category_for(

            support,

            technology,

            payload
        ),

        rca_level=support,

        needs_human_review=review[
            "requires_review"
        ],

        recommended_next_action=recommended_next_action,

        source_event_id=payload.get("source_event_id"),

        normalised_event_id=payload.get("normalised_event_id")
    )

