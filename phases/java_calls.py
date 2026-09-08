"""Resolve Java call sites to declarations, abstaining on ambiguous targets."""
from models import FileInfo, PipelineState


class JavaSymbols:
    def __init__(self, files: list[FileInfo]):
        self.types = {}
        for file in files:
            if file.language != "java" or file.parse_error:
                continue
            for cls in file.classes:
                qualified = f"{file.package}.{cls.name}" if file.package else cls.name
                self.types.setdefault(qualified, []).append((file, cls))

    def resolve_type(self, file: FileInfo, name: str | None):
        if not name:
            return None
        name = name.split("<", 1)[0].strip()
        if name.endswith("[]") or name == "var":
            return None
        if "." in name:
            keys = [name]
        else:
            # Explicit imports take precedence; never resolve an external import
            # to a same-named class in another package.
            explicit = [i.module for i in file.imports if i.module
                        and "static " not in i.raw and "*" not in i.raw
                        and i.module.rsplit(".", 1)[-1] == name]
            if explicit:
                keys = explicit
            else:
                own = f"{file.package}.{name}" if file.package else name
                if own in self.types:
                    keys = [own]
                else:
                    keys = [f"{i.module}.{name}" for i in file.imports
                            if i.module and "*" in i.raw and "static " not in i.raw]
        candidates = [item for key in set(keys) for item in self.types.get(key, [])]
        return candidates[0] if len(candidates) == 1 else None

    def method(self, owner, name, arity, seen=None):
        if owner is None:
            return None
        file, cls = owner
        key = (file.path, cls.name)
        seen = set(seen or ())
        if key in seen:
            return None
        seen.add(key)
        named = [fn for fn in file.functions if fn.class_name == cls.name and fn.name == name]
        if any(p.type_hint == "..." for fn in named for p in fn.parameters):
            return None
        matches = [fn for fn in named if len(fn.parameters) == arity]
        if matches:
            return (file, matches[0]) if len(matches) == 1 else None
        inherited = [self.method(self.resolve_type(file, base), name, arity, seen)
                     for base in cls.extended_classes + cls.implemented_interfaces]
        found = {(f.path, fn.start_line): (f, fn) for item in inherited
                 if item for f, fn in [item]}
        return next(iter(found.values())) if len(found) == 1 else None


def java_call_batch(state: PipelineState) -> tuple[list[dict], int]:
    symbols = JavaSymbols(state.files)
    batch, unresolved = [], 0
    for file in state.files:
        if file.language != "java":
            continue
        if file.parse_error:
            raise ValueError(f"Cannot resolve calls for invalid Java file: {file.path}")
        for fn in file.functions:
            for site in fn.call_sites:
                target = symbols.method(symbols.resolve_type(file, site.receiver_type),
                                        site.name, site.argument_count)
                if target is None:
                    unresolved += 1
                    continue
                callee_file, callee = target
                batch.append({
                    "caller_name": fn.name, "caller_file": file.path,
                    "caller_start_line": fn.start_line,
                    "callee_name": callee.name, "callee_file": callee_file.path,
                    "callee_start_line": callee.start_line,
                    "knowledge_id": state.knowledge_id,
                    "resolution": "declared_receiver", "call_line": site.line,
                    "receiver": site.receiver or "this",
                })
    return batch, unresolved
