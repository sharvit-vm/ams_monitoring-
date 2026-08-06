"""
Phase 1 - Scanner
Walks the repo, collects all files, detects language, returns list of FileInfo.
"""

import os
import json
import hashlib
from pathlib import Path
from typing import List
from tqdm import tqdm
from models import FileInfo, PipelineState
from parsers.language_detector import detect_language, should_skip_dir, is_parseable
from config import CACHE_DIR
SCAN_CACHE_FILE = "scan_result.json"

def _cache_path(state: PipelineState) -> Path:
    cache_dir = Path(CACHE_DIR) / state.knowledge_id
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / SCAN_CACHE_FILE

def _file_hash(file_path: str) -> str:
    """MD5 hash of file contents - used as cache key."""
    with open(file_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()

def scan_repo(state: PipelineState) -> PipelineState:
    """
    Walks the repo at state.repo_path.
    Populates state.files with one FileInfo per discovered file.
    Skips ignored dirs/extensions.
    Caches result to disk - re-running is instant if cache exists.
    """
    cache_file = _cache_path(state)

    if cache_file.exists():
        print(f"[Scanner] Cache found at {cache_file} - loading.")
        with open(cache_file) as f:
            raw = json.load(f)
        state.files = [FileInfo(**item) for item in raw]
        state.scan_complete = True
        print(f"[Scanner] Loaded {len(state.files)} files from cache.\n")
        return state

    print(f"[Scanner] Scanning repo: {state.repo_path}")
    repo_root = Path(state.repo_path).resolve()
    discovered: List[FileInfo] = []

    all_paths = []
    for root, dirs, files in os.walk(repo_root):

        dirs[:] = [d for d in dirs if not should_skip_dir(d)]

        for filename in files:
            all_paths.append(os.path.join(root, filename))

    print(f"[Scanner] Found {len(all_paths)} total files before language filtering.")

    skipped = 0
    for abs_path in tqdm(all_paths, desc="Scanning files"):
        language = detect_language(abs_path)

        if language is None:
            skipped += 1
            continue

        rel_path = str(Path(abs_path).relative_to(repo_root))

        file_info = FileInfo(
            path=rel_path,
            absolute_path=abs_path,
            language=language,
            total_lines=_count_lines(abs_path),
        )
        discovered.append(file_info)

    lang_counts: dict = {}
    for f in discovered:
        lang_counts[f.language] = lang_counts.get(f.language, 0) + 1

    print(f"\n[Scanner] Results:")
    print(f"  Total discovered : {len(discovered)}")
    print(f"  Skipped (unknown): {skipped}")
    print(f"  Languages found  :")
    for lang, count in sorted(lang_counts.items(), key=lambda x: -x[1]):
        parseable_marker = "[ok] parseable" if is_parseable(lang) else "  stored only"
        print(f"    {lang:<20} {count:>4} files   {parseable_marker}")

    with open(cache_file, "w") as f:
        json.dump([fi.model_dump() for fi in discovered], f, indent=2)
    print(f"\n[Scanner] Cached to {cache_file}")

    state.files = discovered
    state.scan_complete = True
    return state

def _count_lines(file_path: str) -> int:
    try:
        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for _ in f)
    except Exception:
        return 0

if __name__ == "__main__":
    import sys
    import uuid

    if len(sys.argv) < 2:
        print("Usage: python phases/scanner.py <repo_path>")
        sys.exit(1)

    repo_path = sys.argv[1]
    state = PipelineState(repo_path=repo_path, knowledge_id=str(uuid.uuid4())[:8])
    state = scan_repo(state)
    print(f"\nFirst 10 files:")
    for f in state.files[:10]:
        print(f"  [{f.language:<12}] {f.path}")


