"""
Phase 4 - Hierarchy Building
Groups files into L1-L8 folder nodes bottom-up.
Calls LLM once per folder to generate a summary.
"""
import json
import time
from pathlib import Path
from typing import Dict, List
from collections import defaultdict
from tqdm import tqdm
from langchain_core.prompts import PromptTemplate
from langchain_core.output_parsers import JsonOutputParser
from models import FileInfo, LevelNode, PipelineState, RepoSummary
from config import CACHE_DIR, MAX_HIERARCHY_LEVELS, llm
prompt = PromptTemplate.from_template("""
You are analyzing a folder in a software repository.
Folder: {folder_path}
Languages: {languages}
Total files: {file_count}
What's inside:
{child_summaries}
Give me:
1. summary - 2-3 sentences about what this folder contains
2. purpose - a short phrase (max 8 words) describing its role
Reply ONLY in this JSON format:
{{"summary": "...", "purpose": "..."}}
""")
chain = prompt | llm | JsonOutputParser()
def get_hierarchy_cache_dir(state: PipelineState) -> Path:
    d = Path(CACHE_DIR) / state.knowledge_id / "hierarchy"
    d.mkdir(parents=True, exist_ok=True)
    return d
def save_hierarchy_cache(cache_dir: Path, folder_path: str, data: dict, level: int):
    level_dir = cache_dir / f"L{level}"
    level_dir.mkdir(parents=True, exist_ok=True)
    filename = folder_path.replace("/", "__").replace("\\", "__") + ".json"
    with open(level_dir / filename, "w") as f:
        json.dump(data, f, indent=2)
def load_hierarchy_cache(cache_dir: Path, folder_path: str, level: int) -> dict | None:
    level_dir = cache_dir / f"L{level}"
    filename = folder_path.replace("/", "__").replace("\\", "__") + ".json"
    p = level_dir / filename
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return None
def get_parent(path: str) -> str | None:
    parent = str(Path(path).parent)
    if parent in (".", path, ""):
        return None
    return parent
def normalize(path: str) -> str:
    return path.replace("\\", "/")
def summarize_folder(folder_path: str, child_summaries: List[str], languages: List[str], file_count: int) -> tuple:
    try:
        result = chain.invoke({
            "folder_path": folder_path,
            "languages": ", ".join(languages) or "unknown",
            "file_count": file_count,
            "child_summaries": "\n".join(f"- {s}" for s in child_summaries[:12]) or "no summaries available",
        })
        return result.get("summary"), result.get("purpose")
    except Exception as e:
        print(f"\n  [Error] folder {folder_path}: {e}")
        return None, None
def build_hierarchy(state: PipelineState) -> PipelineState:
    if state.hierarchy_complete:
        print("[Hierarchy] Already done, skipping.")
        return state
    cache_dir = get_hierarchy_cache_dir(state)
    hierarchy: Dict[str, LevelNode] = {}
    print("\n[Hierarchy] Building L1 nodes...")
    folder_files: Dict[str, List[FileInfo]] = defaultdict(list)
    for f in state.files:
        parent = str(Path(normalize(f.path)).parent)
        if parent == ".":
            parent = "(root)"
        folder_files[parent].append(f)
    for folder_path, files in tqdm(folder_files.items(), desc="L1 nodes"):
        cached = load_hierarchy_cache(cache_dir, folder_path, level=1)
        if cached:
            hierarchy[folder_path] = LevelNode(**cached)
            continue
        languages = list(set(f.language for f in files))
        child_summaries = [
            f"{Path(f.path).name}: {f.purpose}"
            for f in files if f.purpose
        ]
        summary, purpose = summarize_folder(folder_path, child_summaries, languages, len(files))
        time.sleep(0.1)
        node = LevelNode(
            path=folder_path,
            level=1,
            files=[f.path for f in files],
            languages=languages,
            file_count=len(files),
            summary=summary,
            purpose=purpose,
            parent_path=get_parent(folder_path),
        )
        save_hierarchy_cache(cache_dir, folder_path, node.model_dump(), level=1)
        hierarchy[folder_path] = node
    # Exclude "(root)" so it doesn't block L2+ parent traversal
    current_paths = {path for path in folder_files if path != "(root)"}
    for level in range(2, MAX_HIERARCHY_LEVELS + 1):
        parent_children: Dict[str, List[str]] = defaultdict(list)
        for path in current_paths:
            p = get_parent(path) or "(root)"
            parent_children[p].append(path)
        new_paths = set(parent_children.keys()) - current_paths
        if not new_paths:
            break
        print(f"[Hierarchy] Building L{level} nodes ({len(new_paths)} folders)...")
        for parent_path, children in tqdm(parent_children.items(), desc=f"L{level} nodes"):
            if parent_path in hierarchy:
                continue
            cached = load_hierarchy_cache(cache_dir, parent_path, level=level)
            if cached:
                hierarchy[parent_path] = LevelNode(**cached)
                continue
            all_langs = list(set(
                lang
                for c in children if c in hierarchy
                for lang in hierarchy[c].languages
            ))
            total_files = sum(hierarchy[c].file_count for c in children if c in hierarchy)
            child_summaries = [
                f"{Path(c).name}/: {hierarchy[c].purpose}"
                for c in children if c in hierarchy and hierarchy[c].purpose
            ]
            summary, purpose = summarize_folder(parent_path, child_summaries, all_langs, total_files)
            time.sleep(0.1)
            node = LevelNode(path=parent_path,level=level,subfolders=children,languages=all_langs,file_count=total_files,summary=summary,purpose=purpose,parent_path=get_parent(parent_path),)
            save_hierarchy_cache(cache_dir, parent_path, node.model_dump(), level=level)
            hierarchy[parent_path] = node
        current_paths = set(parent_children.keys())
    print("\n[Hierarchy] Generating repo summary...")
    max_actual_level = max((n.level for n in hierarchy.values()), default=1)
    top_summaries = [
        n.purpose for n in hierarchy.values()
        if n.level == max_actual_level and n.purpose
    ][:8]
    repo_summary, repo_purpose = summarize_folder(
        state.repo_path,
        top_summaries,
        list(set(f.language for f in state.files)),
        len(state.files)
    )
    state.repo_summary = RepoSummary(
        repo_path=state.repo_path,
        knowledge_id=state.knowledge_id,
        org_id=state.org_id,
        total_files=len(state.files),
        languages=list(set(f.language for f in state.files)),
        summary=repo_summary,
        purpose=repo_purpose,
    )
    repo_dir = cache_dir / "repo"
    repo_dir.mkdir(parents=True, exist_ok=True)
    with open(repo_dir / "summary.json", "w") as f:
        json.dump(state.repo_summary.model_dump(), f, indent=2)
    print(f"\n[Hierarchy] Results:")
    print(f"Total nodes  : {len(hierarchy)}")
    print(f"Repo purpose : {state.repo_summary.purpose}")
    state.hierarchy = hierarchy
    state.hierarchy_complete = True
    return state
if __name__ == "__main__":
    import sys, uuid
    from phases.scanner import scan_repo
    from phases.file_analysis import analyze_files
    from phases.llm_analysis import analyze_with_llm
    if len(sys.argv) < 2:
        print("Usage: python -m phases.hierarchy <repo_path>")
        sys.exit(1)
    state = PipelineState(repo_path=sys.argv[1], knowledge_id=str(uuid.uuid4())[:8])
    state = scan_repo(state)
    state = analyze_files(state)
    state = analyze_with_llm(state)
    state = build_hierarchy(state)
    print("\nSample nodes:")
    for path, node in list(state.hierarchy.items())[:5]:
        print(f"\n  [L{node.level}] {path}")
        print(f"  Purpose : {node.purpose}")
        print(f"  Files   : {node.file_count}")
