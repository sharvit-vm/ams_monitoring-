"""
Used by RCA Agent and Code Fix Agent to query the Neo4j graph.
"""
from langchain_core.tools import tool
from config import get_neo4j_driver

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
        WITH collect(DISTINCT imported.path) +
             collect(DISTINCT imported2.path) +
             collect(DISTINCT caller.path) +
             collect(DISTINCT caller2.path) +
             collect(DISTINCT called_file.path) AS all_paths
        UNWIND all_paths AS p
        WITH p WHERE p IS NOT NULL AND p <> $file_path
        RETURN DISTINCT p AS file_path
    """, {"file_path": file_path, "knowledge_id": knowledge_id})
    return [r["file_path"] for r in results]

@tool
def get_function_calls(function_name: str, file_path: str, knowledge_id: str) -> dict:
    """
    Given a function name and file, returns what it calls and what calls it.
    Use this to understand blast radius of a bug.
    """
    calls = run_query("""
        MATCH (fn:FunctionNode {name: $name, file_path: $file_path, knowledge_id: $knowledge_id})
              -[:CALLS]->(called:FunctionNode {knowledge_id: $knowledge_id})
        RETURN DISTINCT called.name AS name, called.file_path AS file_path, called.start_line AS start_line
    """, {"name": function_name, "file_path": file_path, "knowledge_id": knowledge_id})
    called_by = run_query("""
        MATCH (caller:FunctionNode {knowledge_id: $knowledge_id})-[:CALLS]->
              (fn:FunctionNode {name: $name, file_path: $file_path, knowledge_id: $knowledge_id})
        RETURN DISTINCT caller.name AS name, caller.file_path AS file_path, caller.start_line AS start_line
    """, {"name": function_name, "file_path": file_path, "knowledge_id": knowledge_id})
    return {
        "calls":     [{"name": r["name"], "file": r["file_path"], "start_line": r["start_line"]} for r in calls],
        "called_by": [{"name": r["name"], "file": r["file_path"], "start_line": r["start_line"]} for r in called_by],
    }

@tool
def get_file_summary(file_path: str, knowledge_id: str) -> dict:
    """
    Returns the summary, purpose and function list for a file.
    Use this to quickly understand what a file does without reading it.
    """
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
    results = run_query("""
        MATCH (f:FileNode {path: $file_path, knowledge_id: $knowledge_id})
              -[:IN_FOLDER]->(l:LevelNode)
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