from __future__ import annotations
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field
class ParameterInfo(BaseModel):
    name: str
    type_hint: Optional[str] = None

class CallSite(BaseModel):
    name: str
    receiver: Optional[str] = None
    receiver_type: Optional[str] = None
    line: int
    argument_count: int

class FunctionInfo(BaseModel):
    name: str
    file_path: str
    start_line: int
    end_line: int
    parameters: List[ParameterInfo] = Field(default_factory=list)
    return_type: Optional[str] = None
    calls: List[str] = Field(default_factory=list)
    call_sites: List[CallSite] = Field(default_factory=list)
    summary: Optional[str] = None
    is_method: bool = False
    class_name: Optional[str] = None

class ClassInfo(BaseModel):
    name: str
    file_path: str
    start_line: int
    end_line: int
    methods: List[str] = Field(default_factory=list)
    base_classes: List[str] = Field(default_factory=list)
    implemented_interfaces: List[str] = Field(default_factory=list)
    extended_classes: List[str] = Field(default_factory=list)
    is_interface: bool = False
    summary: Optional[str] = None

class ImportInfo(BaseModel):
    raw: str
    module: Optional[str] = None
    is_local: bool = False

class ImportedSymbol(BaseModel):
    name: str
    module: Optional[str] = None
    alias: Optional[str] = None
    is_function: bool = False
    is_class: bool = False

class ExportedSymbol(BaseModel):
    name: str
    type: str  # "function" or "class"
    is_public: bool = True

class FileInfo(BaseModel):
    parser_version: Optional[str] = None
    source_hash: Optional[str] = None
    path: str
    absolute_path: str
    language: str
    package: Optional[str] = None
    imports: List[ImportInfo] = Field(default_factory=list)
    imported_functions: List[ImportedSymbol] = Field(default_factory=list)
    imported_classes: List[ImportedSymbol] = Field(default_factory=list)
    exported_functions: List[ExportedSymbol] = Field(default_factory=list)
    exported_classes: List[ExportedSymbol] = Field(default_factory=list)
    functions: List[FunctionInfo] = Field(default_factory=list)
    classes: List[ClassInfo] = Field(default_factory=list)
    total_lines: int = 0
    summary: Optional[str] = None
    purpose: Optional[str] = None
    parse_error: Optional[str] = None
    llm_processed: bool = False

class LevelNode(BaseModel):
    path: str
    level: int
    files: List[str] = Field(default_factory=list)
    subfolders: List[str] = Field(default_factory=list)
    languages: List[str] = Field(default_factory=list)
    file_count: int = 0
    summary: Optional[str] = None
    purpose: Optional[str] = None
    parent_path: Optional[str] = None

class RepoSummary(BaseModel):
    repo_path: str
    knowledge_id: str
    org_id: Optional[str] = None
    total_files: int = 0
    languages: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    purpose: Optional[str] = None
    summary: Optional[str] = None

class PipelineState(BaseModel):
    repo_path: str
    knowledge_id: str
    org_id: Optional[str] = None
    files: List[FileInfo] = Field(default_factory=list)
    hierarchy: Dict[str, LevelNode] = Field(default_factory=dict)
    repo_summary: Optional[RepoSummary] = None
    scan_complete: bool = False
    file_analysis_complete: bool = False
    llm_analysis_complete: bool = False
    hierarchy_complete: bool = False
    neo4j_complete: bool = False
    vector_complete: bool = False
    class Config:
        arbitrary_types_allowed = True
