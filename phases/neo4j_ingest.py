"""
Phase 5 - Neo4j Ingestion
Reads all cache folder data from Phases 1-4 and writes to Neo4j.

Nodes:
  KnowledgeNode - one per repo ingestion; also holds repo summary fields
                  (repo_path, org_id, purpose, summary, total_files, languages)
  FileNode      - one per file (includes parse_error, function_count, class_count)
  FunctionNode  - one per function (includes param_types)
  ClassNode     - one per class
  LevelNode     - one per folder at each hierarchy level

Relationships:
  (KnowledgeNode)-[:OWNS]->(FileNode)
  (KnowledgeNode)-[:OWNS]->(LevelNode)
  (FileNode)-[:IMPORTS_FUNCTION {alias, is_local}]->(FunctionNode)
  (FileNode)-[:IMPORTS_CLASS    {alias, is_local}]->(ClassNode)
  (FileNode)-[:EXPORTS_FUNCTION {is_public}]->(FunctionNode)
  (FileNode)-[:EXPORTS_CLASS    {is_public}]->(ClassNode)
  (FileNode)-[:IN_FOLDER]->(LevelNode)
  (FunctionNode)-[:BELONGS_TO]->(FileNode)
  (ClassNode)-[:BELONGS_TO]->(FileNode)
  (ClassNode)-[:HAS_METHOD]->(FunctionNode)
  (FunctionNode)-[:CALLS]->(FunctionNode)
  (LevelNode)-[:PARENT]->(LevelNode)
"""

from pathlib import Path
from tqdm import tqdm
from models import PipelineState
from config import get_neo4j_driver


def _run(driver, query: str, params: dict = None):
    with driver.session() as session:
        session.run(query, params or {})


def _run_batch(driver, query: str, batch: list):
    if not batch:
        return
    with driver.session() as session:
        session.run(query, {"batch": batch})


def _normalize(path: str) -> str:
    return path.replace("\\", "/") if path else path


def _quote_constraint_name(name: str) -> str:
    return f"`{name.replace('`', '``')}`"


def _drop_obsolete_identity_constraints(driver, label: str, desired_properties: list[str]):
    """Remove older uniqueness constraints that conflict with current MERGE keys."""
    desired = set(desired_properties)
    with driver.session() as session:
        rows = session.run("""
            SHOW CONSTRAINTS
            YIELD name, labelsOrTypes, properties
            WHERE $label IN labelsOrTypes
            RETURN name, properties
        """, {"label": label})
        constraints = [(row["name"], row["properties"]) for row in rows]

        for name, properties in constraints:
            prop_set = set(properties or [])
            if prop_set and prop_set != desired and prop_set.issubset(desired):
                session.run(f"DROP CONSTRAINT {_quote_constraint_name(name)} IF EXISTS")
                print(f"[Neo4j] Dropped obsolete {label} constraint: {name}")


def create_constraints(driver):
    _drop_obsolete_identity_constraints(
        driver,
        "FunctionNode",
        ["name", "file_path", "start_line", "knowledge_id"],
    )
    constraints = [
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:KnowledgeNode) REQUIRE (n.knowledge_id) IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:FileNode) REQUIRE (n.path, n.knowledge_id) IS NODE KEY",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:LevelNode) REQUIRE (n.path, n.knowledge_id) IS NODE KEY",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:FunctionNode) REQUIRE (n.name, n.file_path, n.start_line, n.knowledge_id) IS UNIQUE",
        "CREATE CONSTRAINT IF NOT EXISTS FOR (n:ClassNode) REQUIRE (n.name, n.file_path, n.knowledge_id) IS NODE KEY",
    ]
    for c in constraints:
        try:
            _run(driver, c)
        except Exception as e:
            print(f"[Neo4j] Constraint skipped: {e}")


def create_knowledge_node(driver, state: PipelineState):
    rs = state.repo_summary
    _run(driver, """
        MERGE (k:KnowledgeNode {knowledge_id: $knowledge_id})
        SET k.name        = $name,
            k.repo_path   = $repo_path,
            k.org_id      = $org_id,
            k.purpose     = $purpose,
            k.summary     = $summary,
            k.total_files = $total_files,
            k.languages   = $languages
    """, {
        "knowledge_id": state.knowledge_id,
        "name":         Path(state.repo_path).name or state.repo_path,
        "repo_path":    state.repo_path,
        "org_id":       state.org_id or "",
        "purpose":      rs.purpose or "" if rs else "",
        "summary":      rs.summary or "" if rs else "",
        "total_files":  rs.total_files if rs else 0,
        "languages":    rs.languages if rs else [],
    })


def create_file_nodes(driver, state: PipelineState):
    batch = [
        {
            "path":           f.path,
            "name":           Path(f.path).name,
            "language":       f.language,
            "package":        f.package or "",
            "summary":        f.summary or "",
            "purpose":        f.purpose or "",
            "total_lines":    f.total_lines,
            "parse_error":    f.parse_error or "",
            "function_count": len(f.functions),
            "class_count":    len(f.classes),
            "import_count":   len(f.imports),
            "knowledge_id":   state.knowledge_id,
        }
        for f in state.files
    ]
    _run_batch(driver, """
        UNWIND $batch AS f
        MERGE (n:FileNode {path: f.path, knowledge_id: f.knowledge_id})
        SET n.name           = f.name,
            n.language       = f.language,
            n.package        = f.package,
            n.summary        = f.summary,
            n.purpose        = f.purpose,
            n.total_lines    = f.total_lines,
            n.parse_error    = f.parse_error,
            n.function_count = f.function_count,
            n.class_count    = f.class_count,
            n.import_count   = f.import_count
        WITH n, f
        MATCH (k:KnowledgeNode {knowledge_id: f.knowledge_id})
        MERGE (k)-[:OWNS]->(n)
    """, batch)


def create_function_nodes(driver, state: PipelineState):
    batch = [
        {
            "name":         func.name,
            "file_path":    f.path,
            "start_line":   func.start_line,
            "end_line":     func.end_line,
            "return_type":  func.return_type or "",
            "is_method":    func.is_method,
            "class_name":   func.class_name or "",
            "parameters":   [p.name for p in func.parameters],
            "param_types":  [p.type_hint or "" for p in func.parameters],
            "knowledge_id": state.knowledge_id,
        }
        for f in state.files
        for func in f.functions
    ]
    _run_batch(driver, """
        UNWIND $batch AS fn
        MERGE (n:FunctionNode {name: fn.name, file_path: fn.file_path, start_line: fn.start_line, knowledge_id: fn.knowledge_id})
        SET n.start_line  = fn.start_line,
            n.end_line    = fn.end_line,
            n.return_type = fn.return_type,
            n.is_method   = fn.is_method,
            n.class_name  = fn.class_name,
            n.parameters  = fn.parameters,
            n.param_types = fn.param_types
        WITH n, fn
        MATCH (f:FileNode {path: fn.file_path, knowledge_id: fn.knowledge_id})
        MERGE (n)-[:BELONGS_TO]->(f)
    """, batch)


def create_class_nodes(driver, state: PipelineState):
    batch = [
        {
            "name":         cls.name,
            "file_path":    f.path,
            "start_line":   cls.start_line,
            "end_line":     cls.end_line,
            "base_classes": cls.base_classes,
            "methods":      cls.methods,
            "knowledge_id": state.knowledge_id,
        }
        for f in state.files
        for cls in f.classes
    ]
    _run_batch(driver, """
        UNWIND $batch AS cls
        MERGE (n:ClassNode {name: cls.name, file_path: cls.file_path, knowledge_id: cls.knowledge_id})
        SET n.start_line   = cls.start_line,
            n.end_line     = cls.end_line,
            n.base_classes = cls.base_classes,
            n.methods      = cls.methods
        WITH n, cls
        MATCH (f:FileNode {path: cls.file_path, knowledge_id: cls.knowledge_id})
        MERGE (n)-[:BELONGS_TO]->(f)
    """, batch)


def create_level_nodes(driver, state: PipelineState):
    batch = [
        {
            "path":         node.path,
            "name":         Path(node.path).name or node.path,
            "level":        node.level,
            "summary":      node.summary or "",
            "purpose":      node.purpose or "",
            "file_count":   node.file_count,
            "languages":    node.languages,
            "knowledge_id": state.knowledge_id,
        }
        for node in state.hierarchy.values()
    ]
    _run_batch(driver, """
        UNWIND $batch AS ln
        MERGE (n:LevelNode {path: ln.path, knowledge_id: ln.knowledge_id})
        SET n.name       = ln.name,
            n.level      = ln.level,
            n.summary    = ln.summary,
            n.purpose    = ln.purpose,
            n.file_count = ln.file_count,
            n.languages  = ln.languages
        WITH n, ln
        MATCH (k:KnowledgeNode {knowledge_id: ln.knowledge_id})
        MERGE (k)-[:OWNS]->(n)
    """, batch)


def create_file_in_folder_relationships(driver, state: PipelineState):
    batch = [
        {
            "file_path":    file_path,
            "folder_path":  node.path,
            "knowledge_id": state.knowledge_id,
        }
        for node in state.hierarchy.values()
        if node.level == 1
        for file_path in node.files
    ]
    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (f:FileNode {path: r.file_path, knowledge_id: r.knowledge_id})
        MATCH (l:LevelNode {path: r.folder_path, knowledge_id: r.knowledge_id})
        MERGE (f)-[:IN_FOLDER]->(l)
    """, batch)


def create_level_parent_relationships(driver, state: PipelineState):
    # Normalize hierarchy keys to forward slashes for consistent lookup
    normalized_hierarchy = {_normalize(k): v for k, v in state.hierarchy.items()}

    batch = [
        {
            "child_path":   node.path,
            "parent_path":  _normalize(node.parent_path),
            "knowledge_id": state.knowledge_id,
        }
        for node in state.hierarchy.values()
        if node.parent_path and _normalize(node.parent_path) in normalized_hierarchy
    ]
    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (child:LevelNode {path: r.child_path, knowledge_id: r.knowledge_id})
        MATCH (parent:LevelNode {path: r.parent_path, knowledge_id: r.knowledge_id})
        MERGE (child)-[:PARENT]->(parent)
    """, batch)


def create_class_has_method_relationships(driver, state: PipelineState):
    batch = [
        {
            "class_name":      func.class_name,
            "func_name":       func.name,
            "func_start_line": func.start_line,
            "file_path":       f.path,
            "knowledge_id":    state.knowledge_id,
        }
        for f in state.files
        for func in f.functions
        if func.is_method and func.class_name
    ]
    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (c:ClassNode  {name: r.class_name, file_path: r.file_path, knowledge_id: r.knowledge_id})
        MATCH (fn:FunctionNode {name: r.func_name, file_path: r.file_path, start_line: r.func_start_line, knowledge_id: r.knowledge_id})
        MERGE (c)-[:HAS_METHOD]->(fn)
    """, batch)


def create_calls_relationships(driver, state: PipelineState):
    # Build lookup: function name -> (file_path, start_line) for first occurrence as fallback
    global_func_lookup: dict[str, tuple] = {}
    for f in state.files:
        for func in f.functions:
            if func.name not in global_func_lookup:
                global_func_lookup[func.name] = (f.path, func.start_line)

    # Build per-file function name -> start_line map for same-file resolution
    file_func_info: dict[str, dict[str, int]] = {
        f.path: {func.name: func.start_line for func in f.functions}
        for f in state.files
    }

    batch = []
    for f in state.files:
        for func in f.functions:
            for called_name in func.calls:
                # Prefer same-file match, fall back to global lookup
                if called_name in file_func_info.get(f.path, {}):
                    callee_file = f.path
                    callee_start = file_func_info[f.path][called_name]
                elif called_name in global_func_lookup:
                    callee_file, callee_start = global_func_lookup[called_name]
                else:
                    continue

                batch.append({
                    "caller_name":       func.name,
                    "caller_file":       f.path,
                    "caller_start_line": func.start_line,
                    "callee_name":       called_name,
                    "callee_file":       callee_file,
                    "callee_start_line": callee_start,
                    "knowledge_id":      state.knowledge_id,
                })

    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (caller:FunctionNode {name: r.caller_name, file_path: r.caller_file, start_line: r.caller_start_line, knowledge_id: r.knowledge_id})
        MATCH (callee:FunctionNode {name: r.callee_name, file_path: r.callee_file, start_line: r.callee_start_line, knowledge_id: r.knowledge_id})
        MERGE (caller)-[:CALLS]->(callee)
    """, batch)


def _build_file_lookup(state: PipelineState) -> dict[str, str]:
    """Maps normalized module path -> file_path for local resolution.

    Registers every possible suffix of the path so that dotted imports like
    'agents.supervisor' (-> 'agents/supervisor') match against the full
    relative path 'practise/day10/agents/supervisor'.
    """
    file_lookup: dict[str, str] = {}
    for f in state.files:
        normalized = _normalize(f.path)
        for ext in (".py", ".java", ".js", ".jsx", ".ts", ".tsx", ".go"):
            if normalized.endswith(ext):
                normalized = normalized[:-len(ext)]
                break
        parts = normalized.split("/")
        # Register all trailing suffixes: full path, sub-paths, and filename
        for i in range(len(parts)):
            suffix = "/".join(parts[i:])
            if suffix not in file_lookup:
                file_lookup[suffix] = f.path
            dotted = suffix.replace("/", ".")
            if dotted not in file_lookup:
                file_lookup[dotted] = f.path
        if f.package:
            class_name = Path(f.path).stem
            qualified = f"{f.package}.{class_name}"
            file_lookup.setdefault(qualified, f.path)
            file_lookup.setdefault(qualified.replace(".", "/"), f.path)
    return file_lookup


def create_imports_function_relationships(driver, state: PipelineState):
    file_lookup = _build_file_lookup(state)

    # Build per-file function name -> start_line for resolving import targets
    file_func_start: dict[str, dict[str, int]] = {
        f.path: {func.name: func.start_line for func in f.functions}
        for f in state.files
    }

    batch = []
    for f in state.files:
        for sym in f.imported_functions:
            if not sym.module:
                continue
            module_normalized = sym.module.replace(".", "/")
            target_path = file_lookup.get(module_normalized) or file_lookup.get(sym.module)
            if not target_path or target_path == f.path:
                continue
            start_line = file_func_start.get(target_path, {}).get(sym.name)
            if start_line is None:
                continue  # function not found in target file, skip
            batch.append({
                "from_path":    f.path,
                "func_name":    sym.name,
                "func_path":    target_path,
                "func_start":   start_line,
                "alias":        sym.alias or "",
                "is_local":     True,
                "knowledge_id": state.knowledge_id,
            })

    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (from:FileNode   {path: r.from_path, knowledge_id: r.knowledge_id})
        MATCH (fn:FunctionNode {name: r.func_name, file_path: r.func_path, start_line: r.func_start, knowledge_id: r.knowledge_id})
        MERGE (from)-[rel:IMPORTS_FUNCTION]->(fn)
        SET rel.alias    = r.alias,
            rel.is_local = r.is_local
    """, batch)


def create_imports_class_relationships(driver, state: PipelineState):
    file_lookup = _build_file_lookup(state)

    batch = []
    for f in state.files:
        for sym in f.imported_classes:
            if not sym.module:
                continue
            module_normalized = sym.module.replace(".", "/")
            target_path = file_lookup.get(module_normalized) or file_lookup.get(sym.module)
            if not target_path or target_path == f.path:
                continue
            batch.append({
                "from_path":    f.path,
                "class_name":   sym.name,
                "class_path":   target_path,
                "alias":        sym.alias or "",
                "is_local":     True,
                "knowledge_id": state.knowledge_id,
            })

    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (from:FileNode {path: r.from_path, knowledge_id: r.knowledge_id})
        MATCH (cls:ClassNode {name: r.class_name, file_path: r.class_path, knowledge_id: r.knowledge_id})
        MERGE (from)-[rel:IMPORTS_CLASS]->(cls)
        SET rel.alias    = r.alias,
            rel.is_local = r.is_local
    """, batch)


def create_exports_relationships(driver, state: PipelineState):
    func_batch = [
        {
            "file_path":    f.path,
            "func_name":    sym.name,
            "func_start":   next((fn.start_line for fn in f.functions if fn.name == sym.name), 0),
            "is_public":    sym.is_public,
            "knowledge_id": state.knowledge_id,
        }
        for f in state.files
        for sym in f.exported_functions
    ]
    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (file:FileNode   {path: r.file_path, knowledge_id: r.knowledge_id})
        MATCH (fn:FunctionNode {name: r.func_name, file_path: r.file_path, start_line: r.func_start, knowledge_id: r.knowledge_id})
        MERGE (file)-[rel:EXPORTS_FUNCTION]->(fn)
        SET rel.is_public = r.is_public
    """, func_batch)

    class_batch = [
        {
            "file_path":    f.path,
            "class_name":   sym.name,
            "is_public":    sym.is_public,
            "knowledge_id": state.knowledge_id,
        }
        for f in state.files
        for sym in f.exported_classes
    ]
    _run_batch(driver, """
        UNWIND $batch AS r
        MATCH (file:FileNode {path: r.file_path, knowledge_id: r.knowledge_id})
        MATCH (cls:ClassNode {name: r.class_name, file_path: r.file_path, knowledge_id: r.knowledge_id})
        MERGE (file)-[rel:EXPORTS_CLASS]->(cls)
        SET rel.is_public = r.is_public
    """, class_batch)


def neo4j_ingest(state: PipelineState) -> PipelineState:
    if state.neo4j_complete:
        print("[Neo4j] Already done, skipping.")
        return state

    driver = get_neo4j_driver()

    print("\n[Neo4j] Creating constraints...")
    create_constraints(driver)

    print("[Neo4j] Creating KnowledgeNode...")
    create_knowledge_node(driver, state)

    print(f"[Neo4j] Creating {len(state.files)} FileNodes...")
    create_file_nodes(driver, state)

    total_functions = sum(len(f.functions) for f in state.files)
    print(f"[Neo4j] Creating {total_functions} FunctionNodes...")
    create_function_nodes(driver, state)

    total_classes = sum(len(f.classes) for f in state.files)
    print(f"[Neo4j] Creating {total_classes} ClassNodes...")
    create_class_nodes(driver, state)

    print(f"[Neo4j] Creating {len(state.hierarchy)} LevelNodes...")
    create_level_nodes(driver, state)

    print("[Neo4j] Creating relationships...")
    steps = [
        ("FileNode -[:IN_FOLDER]-> LevelNode",              create_file_in_folder_relationships),
        ("LevelNode -[:PARENT]-> LevelNode",                create_level_parent_relationships),
        ("ClassNode -[:HAS_METHOD]-> FunctionNode",         create_class_has_method_relationships),
        ("FunctionNode -[:CALLS]-> FunctionNode",           create_calls_relationships),
        ("FileNode -[:IMPORTS_FUNCTION]-> FunctionNode",    create_imports_function_relationships),
        ("FileNode -[:IMPORTS_CLASS]-> ClassNode",          create_imports_class_relationships),
        ("FileNode -[:EXPORTS_FUNCTION/EXPORTS_CLASS]->",   create_exports_relationships),
    ]

    for label, fn in tqdm(steps, desc="Relationships"):
        try:
            fn(driver, state)
        except Exception as e:
            print(f"\n  [Error] {label}: {e}")

    print(f"\n[Neo4j] Ingestion complete.")
    print(f"  Files     : {len(state.files)}")
    print(f"  Functions : {total_functions}")
    print(f"  Classes   : {total_classes}")
    print(f"  Folders   : {len(state.hierarchy)}")

    state.neo4j_complete = True
    return state


if __name__ == "__main__":
    import sys
    import uuid
    from phases.scanner import scan_repo
    from phases.file_analysis import analyze_files
    from phases.llm_analysis import analyze_with_llm
    from phases.hierarchy import build_hierarchy

    if len(sys.argv) < 2:
        print("Usage: python -m phases.neo4j_ingest <repo_path> [knowledge_id]")
        sys.exit(1)

    knowledge_id = sys.argv[2] if len(sys.argv) >= 3 else str(uuid.uuid4())[:8]
    state = PipelineState(repo_path=sys.argv[1], knowledge_id=knowledge_id)
    state = scan_repo(state)
    state = analyze_files(state)
    state = analyze_with_llm(state)
    state = build_hierarchy(state)
    state = neo4j_ingest(state)

    print("\nSample - first 5 files ingested:")
    for f in state.files[:5]:
        print(f"  {f.path} - {len(f.functions)} functions, {len(f.classes)} classes")


