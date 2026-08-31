"""Phase 6 - Configurable RAG ingestion.

Builds retrieval chunks from analyzed repository files, writes a local lexical
index, and optionally upserts semantic vectors to the configured provider.
"""

from models import PipelineState
from rag.indexing.index_pipeline import build_rag_index


def vector_ingest(state: PipelineState) -> PipelineState:
    """Backward-compatible phase name used by the existing workflow."""
    return build_rag_index(state)


if __name__ == "__main__":
    import sys
    import uuid
    from phases.scanner import scan_repo
    from phases.file_analysis import analyze_files
    from phases.llm_analysis import analyze_with_llm
    from phases.hierarchy import build_hierarchy
    from phases.neo4j_ingest import neo4j_ingest

    if len(sys.argv) < 2:
        print("Usage: python -m phases.vector_ingest <repo_path>")
        sys.exit(1)

    state = PipelineState(
        repo_path=sys.argv[1],
        knowledge_id=str(uuid.uuid4())[:8]
    )
    state = scan_repo(state)
    state = analyze_files(state)
    state = analyze_with_llm(state)
    state = build_hierarchy(state)
    state = neo4j_ingest(state)
    state = vector_ingest(state)

    print(f"\nKnowledge ID: {state.knowledge_id}")
    print("Use this ID to filter queries in Neo4j and the configured RAG index.")
