from typing import Any, Optional
from pydantic import BaseModel
from telemetry.explainability import Explanation


class ExecutionBlock(BaseModel):
    start_time: str
    end_time: str
    duration_ms: float


class MetricsBlock(BaseModel):
    llm_latency_ms: float = 0.0
    token_usage: int = 0
    input_size: int = 0
    output_size: int = 0
    confidence: float = 0.0
    success: bool = True
    retry_count: int = 0


class APIResponse(BaseModel):
    trace_id: str = ""
    agent: str = "L2 RCA"
    status: str = "SUCCESS"
    decision: str = ""
    confidence: float = 0.0
    explanation: Optional[Explanation] = None
    execution: Optional[ExecutionBlock] = None
    metrics: Optional[MetricsBlock] = None
    message: str = ""
    data: Optional[Any] = None