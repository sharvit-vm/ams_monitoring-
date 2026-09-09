"""
Phase 6 - Pinecone Vector Ingestion

Reads each file, splits it into token-sized chunks, embeds with OpenAI,
and upserts to Pinecone with source code in metadata.
"""

import hashlib
import time
from typing import List

import tiktoken
from tqdm import tqdm
from openai import OpenAI

from models import PipelineState
from config import OPENAI_API_KEY, EMBEDDING_MODEL, MAX_CHUNK_TOKENS, get_pinecone_index
from observability.token_usage import provider_call
openai_client = OpenAI(api_key=OPENAI_API_KEY)
tokenizer = tiktoken.encoding_for_model("text-embedding-3-small")

UPSERT_BATCH = 100  
EMBED_BATCH  = 20    

def count_tokens(text: str) -> int:
    return len(tokenizer.encode(text))


def read_file_lines(absolute_path: str) -> List[str]:
    try:
        with open(absolute_path, "r", encoding="utf-8", errors="ignore") as f:
            return f.readlines()
    except Exception:
        return []


def make_chunk_id(knowledge_id: str, file_path: str, start_line: int) -> str:
    """Stable unique ID - same inputs always produce same ID so upsert never duplicates."""
    raw = f"{knowledge_id}|{file_path}|{start_line}"
    return hashlib.md5(raw.encode()).hexdigest()


def split_by_tokens(text: str, start_line: int, max_tokens: int) -> List[dict]:
    """
    If a chunk is too large, split it line by line into smaller pieces.
    Simple approach: fill up to max_tokens, then start a new chunk.
    """
    lines = text.splitlines(keepends=True)
    chunks = []
    current_lines = []
    current_start = start_line

    for line in lines:
        current_lines.append(line)
        if count_tokens("".join(current_lines)) > max_tokens:
            if len(current_lines) > 1:
                chunk_text = "".join(current_lines[:-1])
                chunk_end = current_start + len(current_lines) - 2
                chunks.append({
                    "text":       chunk_text,
                    "start_line": current_start,
                    "end_line":   chunk_end,
                })
                current_start = chunk_end + 1
                current_lines = [line]

    if current_lines:
        chunks.append({
            "text":       "".join(current_lines),
            "start_line": current_start,
            "end_line":   current_start + len(current_lines) - 1,
        })

    return chunks

def build_chunks(file_info, max_tokens: int) -> List[dict]:
    """Read the full file and split into token-sized chunks."""
    lines = read_file_lines(file_info.absolute_path)
    if not lines:
        return []

    full_text = "".join(lines)
    raw_chunks = split_by_tokens(full_text, start_line=1, max_tokens=max_tokens)

    return [
        {
            "file_path":  file_info.path,
            "start_line": c["start_line"],
            "end_line":   c["end_line"],
            "text":       c["text"],
        }
        for c in raw_chunks
        if c["text"].strip()
    ]

def embed(texts: List[str]) -> List[List[float]]:
    response = provider_call(openai_client.embeddings.create,
        model=EMBEDDING_MODEL,
        input=texts,
    )
    return [item.embedding for item in response.data]


def upload_to_pinecone(index, vectors: List[dict]):
    for i in range(0, len(vectors), UPSERT_BATCH):
        index.upsert(vectors=vectors[i : i + UPSERT_BATCH])

def vector_ingest(state: PipelineState) -> PipelineState:
    if state.vector_complete:
        print("[VectorIngest] Already done, skipping.")
        return state

    index = get_pinecone_index()
    total_chunks = 0
    failed = 0

    print(f"\n[VectorIngest] Processing {len(state.files)} files...")
    for file_info in tqdm(state.files, desc="Embedding files"):
        try:
            chunks = build_chunks(file_info, MAX_CHUNK_TOKENS)
            if not chunks:
                continue

            vectors = []
            for i in range(0, len(chunks), EMBED_BATCH):
                batch = chunks[i : i + EMBED_BATCH]
                embeddings = embed([c["text"] for c in batch])
                for chunk, embedding in zip(batch, embeddings):
                    chunk_id = make_chunk_id(
                        state.knowledge_id,
                        chunk["file_path"],
                        chunk["start_line"],
                    )
                    vectors.append({
                        "id":     chunk_id,
                        "values": embedding,
                        "metadata": {
                            "knowledge_id": state.knowledge_id,
                            "file_path":    chunk["file_path"],
                            "start_line":   chunk["start_line"],
                            "end_line":     chunk["end_line"],
                            "text":         chunk["text"],
                        }
                    })

            upload_to_pinecone(index, vectors)
            total_chunks += len(chunks)
            time.sleep(0.05)
        except Exception as e:
            print(f"\n  [Error] {file_info.path}: {e}")
            failed += 1
    print(f"\n[VectorIngest] Done.")
    print(f"  Files processed : {len(state.files) - failed}")
    print(f"  Failed          : {failed}")
    print(f"  Total vectors   : {total_chunks}")
    state.vector_complete = True
    return state
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
    print("Use this ID to filter queries in both Neo4j and Pinecone.")

