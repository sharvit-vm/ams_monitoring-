import json
import time
from typing import Tuple

from groq import Groq

from l2_rca.config.settings import settings
from l2_rca.llm.prompts import SYSTEM_PROMPT
from l2_rca.telemetry.logger import tlog

MODEL = "llama-3.3-70b-versatile"


class GroqClient:

    def __init__(self):
        self.client = Groq(api_key=settings.GROQ_API_KEY) if settings.GROQ_API_KEY else None

    def _build_fallback_diagnosis(self, context: dict, error: Exception) -> str:
        text_parts = []
        for key in ("title", "description", "message", "summary", "error_type"):
            value = context.get(key)
            if value:
                text_parts.append(str(value))
        combined_text = " ".join(text_parts).lower()

        if any(token in combined_text for token in ["db", "database", "sql", "postgres", "connection"]):
            problem_domain = "database"
            recommended_agent = "db_fix_agent"
            recommended_checks = [
                "Inspect database connectivity",
                "Review recent schema or connection pool changes",
                "Validate credentials and network reachability",
            ]
        elif any(token in combined_text for token in ["latency", "slow", "timeout", "performance"]):
            problem_domain = "performance"
            recommended_agent = "manual_review"
            recommended_checks = [
                "Inspect recent load and resource saturation",
                "Check downstream service response times",
                "Review recent deployment changes",
            ]
        else:
            problem_domain = "general_infrastructure"
            recommended_agent = "manual_review"
            recommended_checks = [
                "Inspect recent incident context",
                "Check related service logs",
                "Escalate for human review if the issue persists",
            ]

        fallback_payload = {
            "problem_domain": problem_domain,
            "recommended_agent": recommended_agent,
            "confidence": 0.25,
            "reason": f"Groq diagnosis unavailable ({error}); used heuristic fallback based on incident context.",
            "recommended_checks": recommended_checks,
            "evidence": [
                "Unable to reach Groq during L2 RCA diagnosis",
                "Fallback was generated from the incoming incident context",
            ],
            "alternative_agents": [{"agent": "manual_review", "confidence": 0.1}],
        }
        return json.dumps(fallback_payload)

    def diagnose(self, context: dict, ticket_id: str = "", correlation_id: str = "") -> Tuple[str, float, int]:
        """
        Returns (content, llm_latency_ms, token_usage)
        """
        prompt = json.dumps(context, indent=2)

        tlog("LLM_REQUEST_SENT", event="LLM_REQUEST_SENT", status="STARTED",
             model=MODEL, extra={
                 "ticket_id": ticket_id,
                 "prompt_chars": len(prompt),
                 "model": MODEL
             })

        t0 = time.perf_counter()

        if self.client is None:
            latency_ms = (time.perf_counter() - t0) * 1000
            fallback = self._build_fallback_diagnosis(context, RuntimeError("GROQ_API_KEY is not configured"))
            tlog("LLM_FALLBACK_USED", event="LLM_FALLBACK_USED", status="FALLBACK",
                 model=MODEL, latency_ms=latency_ms, extra={
                     "ticket_id": ticket_id,
                     "reason": "missing_groq_api_key"
                 })
            return fallback, latency_ms, 0

        try:
            response = self.client.chat.completions.create(
                model=MODEL,
                temperature=0,
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt}
                ]
            )
        except Exception as exc:
            latency_ms = (time.perf_counter() - t0) * 1000
            fallback = self._build_fallback_diagnosis(context, exc)
            tlog("LLM_FALLBACK_USED", event="LLM_FALLBACK_USED", status="FALLBACK",
                 model=MODEL, latency_ms=latency_ms, extra={
                     "ticket_id": ticket_id,
                     "reason": str(exc)
                 })
            return fallback, latency_ms, 0

        latency_ms = (time.perf_counter() - t0) * 1000
        content = response.choices[0].message.content
        token_usage = response.usage.total_tokens if response.usage else 0

        tlog("LLM_RESPONSE_RECEIVED", event="LLM_RESPONSE_RECEIVED", status="SUCCESS",
             model=MODEL, latency_ms=latency_ms, extra={
                 "ticket_id": ticket_id,
                 "response_chars": len(content),
                 "token_usage": token_usage
             })

        return content, latency_ms, token_usage
