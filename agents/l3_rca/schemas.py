from typing import List, Literal

from pydantic import BaseModel, Field
from agents.l3_rca.validation import SourceCitation


class DefectLocation(BaseModel):
    line: int = Field(ge=1)
    justification: str = Field(min_length=1)
    evidence_ids: List[str] = Field(min_length=1)


class RCAAnalysisFacts(BaseModel):
    """Required reasoning checkpoints grounded in collected evidence."""

    observed_value_or_state: str = Field(min_length=1)
    representation_or_type: str = Field(min_length=1)
    execution_path: List[str] = Field(min_length=1)
    related_paths: List[str] = Field(default_factory=list, description="Static possible paths, not reported execution")
    cause_scope: Literal["local_defect", "upstream_trigger", "undetermined"] = "undetermined"
    upstream_trigger: str = "unknown"
    failure_mechanism: str = Field(min_length=1)
    expected_behavior: str = Field(min_length=1)
    source_evidence: List[str] = Field(min_length=1)
    uncertainties: List[str] = Field(default_factory=list)
    citations: List[SourceCitation] = Field(default_factory=list)
    evidence_ids: List[str] = Field(default_factory=list)
    defect_locations: List[DefectLocation] = Field(default_factory=list)


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
    # Structured checkpoints keep the model's explanation tied to observed
    # evidence while preserving the stable fields consumed by codefix.
    analysis_facts: RCAAnalysisFacts
