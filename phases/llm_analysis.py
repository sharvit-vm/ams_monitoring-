"""phase no. 3 llm analysis reads fileInfo and creates summary and purpose for each file"""
import json
import time
from pathlib import Path
from typing import Optional
from tqdm import tqdm
from langchain_openai import ChatOpenAI
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from models import FileInfo, PipelineState
from config import CACHE_DIR, OPENAI_API_KEY
from phases.file_analysis import get_file_cache_dir, get_cache_key, load_file_cache, save_file_cache

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

def should_skip(f: FileInfo) -> bool:
    if f.llm_processed:
        return True
    if f.parse_error:
        return True
    if not f.functions and not f.classes and f.total_lines < 5:
        return True
    return False

def analyze_with_llm(state: PipelineState) -> PipelineState:
    if state.llm_analysis_complete:
        print("[LLMAnalysis] Already done, skipping.")
        return state
    cache_dir = get_file_cache_dir(state)
    to_process = [f for f in state.files if not should_skip(f)]
    print(f"\n[LLMAnalysis] Files to process : {len(to_process)}")
    print(f"[LLMAnalysis] Already skipped  : {len(state.files) - len(to_process)}\n")
    success, failed = 0, 0
    for file_info in tqdm(to_process, desc="Generating summaries"):
        try:
            result = chain.invoke({
                "file_path": file_info.path,
                "language": file_info.language,
                "imports": ", ".join(i.module or i.raw for i in file_info.imports[:10]) or "none",
                "classes": ", ".join(c.name for c in file_info.classes[:8]) or "none",
                "functions": ", ".join(fn.name for fn in file_info.functions[:15]) or "none",
            })

            file_info.summary = result.get("summary")
            file_info.purpose = result.get("purpose")
            file_info.llm_processed = True
            success += 1

        except Exception as e:
            print(f"\n  [Error] {file_info.path}: {e}")
            failed += 1
        save_file_cache(cache_dir, file_info)
        time.sleep(0.1)
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
