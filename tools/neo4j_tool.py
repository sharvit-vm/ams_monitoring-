"""
Used by RCA Agent and Code Fix Agent to query the Neo4j graph.
"""
from langchain_core.tools import tool
from config import get_neo4j_driver


def _normalise_file_path(file_path: str, knowledge_id: str) -> str:
    """Resolve the stored path without assuming the ingestion host OS."""
    paths = sorted({file_path, file_path.replace("\\", "/"), file_path.replace("/", "\\")})
    rows = run_query("""
        MATCH (f:FileNode {knowledge_id: $knowledge_id})
        WHERE f.path IN $paths
        RETURN DISTINCT f.path AS path
    """, {"knowledge_id": knowledge_id, "paths": paths})
    if len(rows) > 1:
        raise ValueError("Ambiguous graph path: multiple separator variants exist")
    return rows[0]["path"] if rows else file_path

def run_query(query: str, params: dict = None) -> list:
    driver = get_neo4j_driver()
    with driver.session() as session:
        result = session.run(query, params or {})
        return [dict(record) for record in result]

@tool
def get_connected_files(file_path: str, knowledge_id: str) -> list[str]:
    """
    Given a file path, returns all files connected to it via
    IMPORTS or CALLS relationships within the same knowledge_id.
    Use this to find all files the agent needs to read.
    """
    file_path = _normalise_file_path(file_path, knowledge_id)
    results = run_query("""
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
        OPTIONAL MATCH (f)-[:IMPORTS_FUNCTION]->(fn:FunctionNode {knowledge_id: $knowledge_id})
                       -[:BELONGS_TO]->(imported:FileNode {knowledge_id: $knowledge_id})
        OPTIONAL MATCH (f)-[:IMPORTS_CLASS]->(cls:ClassNode {knowledge_id: $knowledge_id})
                       -[:BELONGS_TO]->(imported2:FileNode {knowledge_id: $knowledge_id})
        OPTIONAL MATCH (caller:FileNode {knowledge_id: $knowledge_id})-[:IMPORTS_FUNCTION]
                       ->(fn2:FunctionNode {knowledge_id: $knowledge_id})-[:BELONGS_TO]->(f)
        OPTIONAL MATCH (caller2:FileNode {knowledge_id: $knowledge_id})-[:IMPORTS_CLASS]
                       ->(cls2:ClassNode {knowledge_id: $knowledge_id})-[:BELONGS_TO]->(f)
        OPTIONAL MATCH (f)-[:EXPORTS_FUNCTION]->(efn:FunctionNode {knowledge_id: $knowledge_id})
                       -[:CALLS]->(called:FunctionNode {knowledge_id: $knowledge_id})
                       -[:BELONGS_TO]->(called_file:FileNode {knowledge_id: $knowledge_id})
        OPTIONAL MATCH (f)<-[:BELONGS_TO]-(owned_fn:FunctionNode {knowledge_id: $knowledge_id})
                       <-[:CALLS]-(incoming:FunctionNode {knowledge_id: $knowledge_id})
                       -[:BELONGS_TO]->(incoming_file:FileNode {knowledge_id: $knowledge_id})
        WITH collect(DISTINCT imported.path) +
             collect(DISTINCT imported2.path) +
             collect(DISTINCT caller.path) +
             collect(DISTINCT caller2.path) +
             collect(DISTINCT called_file.path) +
             collect(DISTINCT incoming_file.path) AS all_paths
        UNWIND all_paths AS p
        WITH p WHERE p IS NOT NULL AND p <> $file_path
        RETURN DISTINCT p AS file_path
    """, {"file_path": file_path, "knowledge_id": knowledge_id})
    paths = {r["file_path"] for r in results if r.get("file_path")}

    # Use a relationship variable plus a type predicate. This avoids Neo4j
    # warnings when a repository contains only IMPLEMENTS or only EXTENDS.
    type_queries = [
        """
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
              <-[:BELONGS_TO]-(owned:ClassNode {knowledge_id: $knowledge_id})
              -[rel]->(base:ClassNode {knowledge_id: $knowledge_id})
              -[:BELONGS_TO]->(other:FileNode {knowledge_id: $knowledge_id})
        WHERE type(rel) IN ['IMPLEMENTS', 'EXTENDS']
        RETURN DISTINCT other.path AS file_path
        """,
        """
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
              -[:IMPORTS_CLASS]->(imported:ClassNode {knowledge_id: $knowledge_id})
              -[rel]-(related:ClassNode {knowledge_id: $knowledge_id})
              -[:BELONGS_TO]->(other:FileNode {knowledge_id: $knowledge_id})
        WHERE type(rel) IN ['IMPLEMENTS', 'EXTENDS']
        RETURN DISTINCT other.path AS file_path
        """,
        """
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
              -[:EXPORTS_FUNCTION]->(:FunctionNode {knowledge_id: $knowledge_id})
              -[:CALLS]->(:FunctionNode {knowledge_id: $knowledge_id})
              <-[:HAS_METHOD]-(called_class:ClassNode {knowledge_id: $knowledge_id})
              -[rel]->(:ClassNode {knowledge_id: $knowledge_id})
              -[:BELONGS_TO]->(other:FileNode {knowledge_id: $knowledge_id})
        WHERE type(rel) IN ['IMPLEMENTS', 'EXTENDS']
        RETURN DISTINCT other.path AS file_path
        """,
    ]
    for query in type_queries:
        paths.update(
            r["file_path"]
            for r in run_query(query, {"file_path": file_path, "knowledge_id": knowledge_id})
            if r.get("file_path") and r["file_path"] != file_path
        )
    return sorted(paths)

@tool
def get_function_calls(function_name: str, file_path: str, knowledge_id: str) -> dict:
    """Return stored calls and callers; mark interface-dispatch callers as possible."""
    file_path = _normalise_file_path(file_path, knowledge_id)
    params = {"name": function_name, "file_path": file_path, "knowledge_id": knowledge_id}
    calls = run_query("""
        MATCH (fn:FunctionNode {name: $name, file_path: $file_path, knowledge_id: $knowledge_id})
              -[:CALLS]->(called:FunctionNode {knowledge_id: $knowledge_id})
        RETURN DISTINCT called.name AS name, called.file_path AS file_path,
               called.start_line AS start_line, 'direct' AS resolution
    """, params)
    called_by = run_query("""
        MATCH (caller:FunctionNode {knowledge_id: $knowledge_id})-[:CALLS]->
              (fn:FunctionNode {name: $name, file_path: $file_path, knowledge_id: $knowledge_id})
        RETURN DISTINCT caller.name AS name, caller.file_path AS file_path,
               caller.start_line AS start_line, 'direct' AS resolution
    """, params)
    called_by += run_query("""
        MATCH (impl:FunctionNode {name: $name, file_path: $file_path, knowledge_id: $knowledge_id})
              <-[:HAS_METHOD]-(impl_class:ClassNode {knowledge_id: $knowledge_id})
              -[rel]->(base_class:ClassNode {knowledge_id: $knowledge_id})
              -[:HAS_METHOD]->(base_method:FunctionNode {name: $name, knowledge_id: $knowledge_id})
              <-[:CALLS]-(caller:FunctionNode {knowledge_id: $knowledge_id})
        WHERE type(rel) IN ['IMPLEMENTS', 'EXTENDS']
          AND impl.param_types = base_method.param_types
        RETURN DISTINCT caller.name AS name, caller.file_path AS file_path,
               caller.start_line AS start_line, 'possible_interface_dispatch' AS resolution
    """, params)

    def unique(rows):
        result = {}
        for row in rows:
            key = (row["name"], row["file_path"], row["start_line"])
            result.setdefault(key, {"name": row["name"], "file": row["file_path"],
                                   "start_line": row["start_line"],
                                   "resolution": row["resolution"]})
        return sorted(result.values(), key=lambda r: (r["file"], r["start_line"], r["name"]))

    return {"calls": unique(calls), "called_by": unique(called_by)}

@tool
def get_file_summary(file_path: str, knowledge_id: str) -> dict:
    """
    Returns the summary, purpose and function list for a file.
    Use this to quickly understand what a file does without reading it.
    """
    file_path = _normalise_file_path(file_path, knowledge_id)
    results = run_query("""
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
        RETURN f.summary AS summary,
               f.purpose AS purpose,
               f.function_count AS function_count,
               f.class_count AS class_count,
               f.total_lines AS total_lines
    """, {"file_path": file_path, "knowledge_id": knowledge_id})
    if not results:
        return {}
    r = results[0]
    functions = run_query("""
        MATCH (fn:FunctionNode {file_path: $file_path, knowledge_id: $knowledge_id})
        RETURN fn.name AS name, fn.start_line AS start_line, fn.end_line AS end_line
        ORDER BY fn.start_line
    """, {"file_path": file_path, "knowledge_id": knowledge_id})
    return {
        "summary":        r["summary"],
        "purpose":        r["purpose"],
        "function_count": r["function_count"],
        "class_count":    r["class_count"],
        "total_lines":    r["total_lines"],
        "functions":      [{"name": f["name"], "start": f["start_line"], "end": f["end_line"]} for f in functions],
    }

@tool
def get_folder_context(file_path: str, knowledge_id: str) -> dict:
    """
    Returns the folder summary for the folder this file lives in.
    Use this to understand the business context of the module.
    """
    file_path = _normalise_file_path(file_path, knowledge_id)
    results = run_query("""
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
              -[:IN_FOLDER]->(l:LevelNode {knowledge_id: $knowledge_id})
        RETURN l.path AS folder, l.purpose AS purpose, l.summary AS summary
    """, {"file_path": file_path, "knowledge_id": knowledge_id})
    if not results:
        return {}
    r = results[0]
    return {
        "folder":  r["folder"],
        "purpose": r["purpose"],
        "summary": r["summary"],
    }
