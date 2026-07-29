from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RequestMetrics:
    trace_id: str = ""
    ticket_id: str = ""
    start_time: float = 0.0
    end_time: float = 0.0
    duration_ms: float = 0.0
    llm_latency_ms: float = 0.0
    token_usage: int = 0
    input_size: int = 0
    output_size: int = 0
    confidence: float = 0.0
    decision: str = ""
    success: bool = False
    retry_count: int = 0
    error: Optional[str] = None
