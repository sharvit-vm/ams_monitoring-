"""
Reads and writes source code files from the cloned repo on disk.
The active repo and knowledge_id are set by the agent before tool calls.
"""

import os
from contextvars import ContextVar
from langchain_core.tools import tool
from langchain_text_splitters import RecursiveCharacterTextSplitter
import tiktoken
from config import get_neo4j_driver

CLONE_DIR   = os.getenv("CLONE_DIR", "clone")
TOKEN_LIMIT = 3000
_ACTIVE_REPO_DIR = ContextVar("active_repo_dir", default=CLONE_DIR)
_ACTIVE_KNOWLEDGE_ID = ContextVar("active_knowledge_id", default="")

enc = tiktoken.encoding_for_model("gpt-4o-mini")

fallback_splitter = RecursiveCharacterTextSplitter(
    chunk_size=12000,
    chunk_overlap=800,
    separators=["\nclass ", "\ndef ", "\n\n", "\n"]
)


def count_tokens(text: str) -> int:
    return len(enc.encode(text))

def set_tool_context(repo_dir: str, knowledge_id: str):
    repo_token = _ACTIVE_REPO_DIR.set(repo_dir)
    knowledge_token = _ACTIVE_KNOWLEDGE_ID.set(knowledge_id)
    return repo_token, knowledge_token

def reset_tool_context(repo_token, knowledge_token):
    _ACTIVE_KNOWLEDGE_ID.reset(knowledge_token)
    _ACTIVE_REPO_DIR.reset(repo_token)

def get_full_path(file_path: str) -> str:
    return os.path.join(_ACTIVE_REPO_DIR.get(), file_path)

def get_function_boundaries(file_path: str) -> list:
    """Fetches function start/end lines from Neo4j for the active knowledge_id."""
    knowledge_id = _ACTIVE_KNOWLEDGE_ID.get()
    if not knowledge_id:
        return []
    try:
        driver = get_neo4j_driver()
        with driver.session() as session:
            result = session.run("""
                MATCH (fn:FunctionNode {file_path: $path, knowledge_id: $kid})
                RETURN fn.name AS name, fn.start_line AS start, fn.end_line AS end
                ORDER BY fn.start_line
            """, {"path": file_path, "kid": knowledge_id})
            return [dict(r) for r in result]
    except Exception:
        return []

def build_chunk_header(file_path: str, name: str, chunk_type: str, start: int, end: int) -> str:
    return f"# File: {file_path}\n# {chunk_type}: {name}\n# Lines: {start}-{end}\n"


@tool
def read_file(file_path: str) -> str:
    """
    Reads a source code file from the cloned repo.
    If the file is large it splits it into function-level chunks.
    Use this first when investigating a bug.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {full_path}"
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    code = "".join(lines)
    if not code.strip():
        return f"[Empty file]: {file_path}"
    token_count = count_tokens(code)
    if token_count <= TOKEN_LIMIT:
        header = build_chunk_header(file_path, file_path, "File", 1, len(lines))
        return header + "\n" + code
    functions = get_function_boundaries(file_path)
    if not functions:
        raw_chunks = fallback_splitter.split_text(code)
        chunks = []
        for i, chunk in enumerate(raw_chunks):
            header = f"# File: {file_path}\n# Chunk: {i + 1} of {len(raw_chunks)}\n"
            chunks.append(header + "\n" + chunk)
        return "\n\n---chunk---\n\n".join(chunks)
    chunks = []
    for fn in functions:
        fn_code = "".join(lines[fn["start"] - 1 : fn["end"]])
        header = build_chunk_header(file_path, fn["name"], "Function", fn["start"], fn["end"])
        chunks.append(header + "\n" + fn_code)
    return "\n\n---chunk---\n\n".join(chunks)


@tool
def read_file_range(file_path: str, start_line: int, end_line: int) -> str:
    """
    Reads a specific range of lines from a file in the cloned repo.
    Use this when you know exact line numbers and only need that section.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {full_path}"
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    selected = lines[start_line - 1 : end_line]
    header = build_chunk_header(file_path, "range", "Lines", start_line, end_line)
    return header + "\n" + "".join(selected)


@tool
def write_fix(file_path: str, start_line: int, end_line: int, new_code: str) -> str:
    """
    Writes a fix to the cloned repo by replacing lines start_line to end_line
    with new_code. Only call this after you have read the file and are sure of the fix.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return f"[Error] File not found: {full_path}"
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()
    new_lines = new_code.splitlines(keepends=True)
    if new_lines and not new_lines[-1].endswith("\n"):
        new_lines[-1] += "\n"
    lines[start_line - 1 : end_line] = new_lines
    with open(full_path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return f"Fix applied to {file_path} lines {start_line}-{end_line}"


@tool
def get_token_count(file_path: str) -> dict:
    """
    Returns token count and line count of a file.
    Use this before reading a large file to understand its size.
    """
    full_path = get_full_path(file_path)
    if not os.path.exists(full_path):
        return {"error": f"File not found: {full_path}"}
    with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
        code = f.read()
    lines = code.splitlines()
    tokens = count_tokens(code)
    return {
        "file_path":   file_path,
        "token_count": tokens,
        "line_count":  len(lines),
        "will_chunk":  tokens > TOKEN_LIMIT,
    }
