from typing import List

from pydantic import BaseModel, Field


class L3RCAResult(BaseModel):
    root_cause: str
    buggy_file: str
    buggy_function: str
    buggy_lines: List[int]
    affected_files: List[str]
    fix_suggestion: str
    confidence: str
    confidence_score: float = 0.0
    confidence_breakdown: dict = Field(default_factory=dict)
    reasoning: str
    evidence: List[str] = Field(default_factory=list)
    evidence_records: List[dict] = Field(default_factory=list)
    token_usage: dict = Field(default_factory=dict)
