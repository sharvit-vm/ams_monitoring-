"""phase no. 3 llm analysis reads fileInfo and creates summary and purpose for each file"""
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Optional
from tqdm import tqdm
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from models import FileInfo, PipelineState
from config import CACHE_DIR, OPENAI_API_KEY
from phases.file_analysis import get_file_cache_dir, get_cache_key, load_file_cache, save_file_cache
from observability.token_usage import usage_config

llm = ChatOpenAI(
    model="gpt-4o-mini",
    temperature=0.1,
    api_key=OPENAI_API_KEY
)

prompt = PromptTemplate.from_template("""
You are analysing a source code file and thats your task

File: {file_path}
Language: {language}
Imports: {imports}
Classes: {classes}
Functions: {functions}
Give me:
1. summary - 2-3 sentences about what this file does
2. purpose - a short phrase (max 8 words) describing its role

Reply ONLY in this JSON format:
{{"summary": "...", "purpose": "..."}}
""")
parser = JsonOutputParser()
chain = prompt | llm | parser

MAX_WORKERS = max(1, int(os.getenv("LLM_ANALYSIS_MAX_WORKERS", "4")))
BATCH_SIZE = max(1, int(os.getenv("LLM_ANALYSIS_BATCH_SIZE", str(MAX_WORKERS))))
BATCH_PAUSE_SECONDS = float(os.getenv("LLM_ANALYSIS_BATCH_PAUSE_SECONDS", "0.1"))

def should_skip(f: FileInfo) -> bool:
    if f.llm_processed:
        return True
    if f.parse_error:
        return True
    if not f.functions and not f.classes and f.total_lines < 5:
        return True
    return False


def _analyse_file(file_info: FileInfo, llm_config: dict | None = None) -> tuple[FileInfo, bool, str | None]:
    try:
        result = chain.invoke({
            "file_path": file_info.path,
            "language": file_info.language,
            "imports": ", ".join(i.module or i.raw for i in file_info.imports[:10]) or "none",
            "classes": ", ".join(c.name for c in file_info.classes[:8]) or "none",
            "functions": ", ".join(fn.name for fn in file_info.functions[:15]) or "none",
        }, config=llm_config or {})
        file_info.summary = result.get("summary")
        file_info.purpose = result.get("purpose")
        file_info.llm_processed = True
        return file_info, True, None
    except Exception as e:
        return file_info, False, str(e)


def _chunks(items: list[FileInfo], size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]

def analyze_with_llm(state: PipelineState) -> PipelineState:
    if state.llm_analysis_complete:
        print("[LLMAnalysis] Already done, skipping.")
        return state
    cache_dir = get_file_cache_dir(state)
    to_process = [f for f in state.files if not should_skip(f)]
    print(f"\n[LLMAnalysis] Files to process : {len(to_process)}")
    print(f"[LLMAnalysis] Already skipped  : {len(state.files) - len(to_process)}\n")
    success, failed = 0, 0
    print(f"[LLMAnalysis] Parallel workers : {MAX_WORKERS}")
    print(f"[LLMAnalysis] Batch size       : {BATCH_SIZE}")
    llm_config = usage_config()

    with tqdm(total=len(to_process), desc="Generating summaries") as progress:
        for batch in _chunks(to_process, BATCH_SIZE):
            with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(batch))) as executor:
                futures = [executor.submit(_analyse_file, file_info, llm_config) for file_info in batch]
                for future in as_completed(futures):
                    file_info, ok, error = future.result()
                    if ok:
                        success += 1
                    else:
                        print(f"\n  [Error] {file_info.path}: {error}")
                        failed += 1
                    save_file_cache(cache_dir, file_info)
                    progress.update(1)
            if BATCH_PAUSE_SECONDS > 0:
                time.sleep(BATCH_PAUSE_SECONDS)
    state.files = [load_file_cache(cache_dir, f.path) or f for f in state.files]

    print(f"\n[LLMAnalysis] Done - success: {success}, failed: {failed}")
    state.llm_analysis_complete = True
    return state

if __name__ == "__main__":
    import sys, uuid
    from phases.scanner import scan_repo
    from phases.file_analysis import analyze_files

    if len(sys.argv) < 2:
        print("Usage: python -m phases.llm_analysis <repo_path>")
        sys.exit(1)

    state = PipelineState(repo_path=sys.argv[1], knowledge_id=str(uuid.uuid4())[:8])
    state = scan_repo(state)
    state = analyze_files(state)
    state = analyze_with_llm(state)

    print("\nSample summaries:")
    for f in state.files[:5]:
        if f.summary:
            print(f"\n  {f.path}")
            print(f"  Purpose : {f.purpose}")
            print(f"  Summary : {f.summary[:100]}...")
