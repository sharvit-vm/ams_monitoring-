"""Neo4j expansion for RAG/traceback evidence candidates."""

from __future__ import annotations

from rag.domain.schemas import EvidenceCandidate
from tools.neo4j_tool import get_connected_files, get_file_summary, get_folder_context, get_function_calls


def _invoke_tool(tool, payload: dict):
    try:
        return tool.invoke(payload)
    except Exception as exc:
        return {"error": str(exc)}


def expand_graph_context(candidate: EvidenceCandidate | None, knowledge_id: str, max_connected_files: int = 5) -> dict:
    if candidate is None or not candidate.file_path:
        return {"status": "skipped", "reason": "No file candidate available for Neo4j expansion."}

    file_path = candidate.file_path
    graph_context = {
        "status": "completed",
        "seed": candidate.model_dump(mode="json"),
        "file_summary": _invoke_tool(get_file_summary, {"file_path": file_path, "knowledge_id": knowledge_id}),
        "folder_context": _invoke_tool(get_folder_context, {"file_path": file_path, "knowledge_id": knowledge_id}),
        "connected_files": [],
        "function_calls": {},
    }

    connected_files = _invoke_tool(get_connected_files, {"file_path": file_path, "knowledge_id": knowledge_id})
    if isinstance(connected_files, list):
        graph_context["connected_files"] = connected_files[:max_connected_files]
    else:
        graph_context["connected_files_error"] = connected_files

    if candidate.symbol_name:
        graph_context["function_calls"] = _invoke_tool(
            get_function_calls,
            {
                "function_name": candidate.symbol_name,
                "file_path": file_path,
                "knowledge_id": knowledge_id,
            },
        )

    return graph_context
