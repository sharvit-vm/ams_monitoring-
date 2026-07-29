import json
import time
from typing import Tuple

from groq import Groq

from config.settings import settings
from llm.prompts import SYSTEM_PROMPT
from telemetry.logger import tlog
from telemetry.tracer import get_trace_id

MODEL = "llama-3.3-70b-versatile"


class GroqClient:

    def __init__(self):
        self.client = Groq(api_key=settings.GROQ_API_KEY)

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

        response = self.client.chat.completions.create(
            model=MODEL,
            temperature=0,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": prompt}
            ]
        )

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
