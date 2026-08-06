"""
Python parser using tree-sitter.
Extracts functions, classes, imports from .py files.
"""

from typing import List, Optional
import tree_sitter_python as tspython
from tree_sitter import Language, Parser

from models import FileInfo, FunctionInfo, ClassInfo, ImportInfo, ParameterInfo, ImportedSymbol, ExportedSymbol
from parsers.base_parser import BaseParser

PY_LANGUAGE = Language(tspython.language())

class PythonParser(BaseParser):

    def __init__(self):
        self.parser = Parser(PY_LANGUAGE)

    def parse(self, file_info: FileInfo) -> FileInfo:
        try:
            source = self._read_source(file_info.absolute_path)
            tree = self.parser.parse(source)

            file_info.functions = self.extract_functions(source, tree)
            file_info.classes   = self.extract_classes(source, tree)
            file_info.imports   = self.extract_imports(source, tree)
            file_info.imported_functions, file_info.imported_classes = self.extract_imported_symbols(source, tree)
            file_info.exported_functions, file_info.exported_classes = self.extract_exported_symbols(source, tree, file_info)

            class_map = {c.name: c for c in file_info.classes}
            for func in file_info.functions:
                if func.is_method and func.class_name and func.class_name in class_map:
                    class_map[func.class_name].methods.append(func.name)

        except Exception as e:
            file_info.parse_error = str(e)

        return file_info

    def extract_functions(self, source: bytes, tree) -> List[FunctionInfo]:
        functions = []
        self._walk_functions(tree.root_node, source, functions, parent_class=None)
        return functions

    def _walk_functions(self, node, source: bytes, results: list, parent_class: Optional[str]):
        for child in node.children:
            if child.type == "function_definition":
                func = self._parse_function(child, source, parent_class)
                if func:
                    results.append(func)

                self._walk_functions(child, source, results, parent_class)

            elif child.type == "class_definition":
                class_name = self._get_child_text(child, "identifier", source)
                body = self._get_child_by_type(child, "block")
                if body:
                    self._walk_functions(body, source, results, parent_class=class_name)
            else:
                self._walk_functions(child, source, results, parent_class)

    def _parse_function(self, node, source: bytes, parent_class: Optional[str]) -> Optional[FunctionInfo]:
        name_node = self._get_child_by_type(node, "identifier")
        if not name_node:
            return None

        name = self._get_node_text(name_node, source)
        params = self._extract_parameters(node, source)
        return_type = self._extract_return_type(node, source)
        calls = self._extract_calls(node, source)

        return FunctionInfo(
            name=name,
            file_path="",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parameters=params,
            return_type=return_type,
            calls=calls,
            is_method=parent_class is not None,
            class_name=parent_class,
        )

    def _extract_parameters(self, func_node, source: bytes) -> List[ParameterInfo]:
        params = []
        params_node = self._get_child_by_type(func_node, "parameters")
        if not params_node:
            return params

        for child in params_node.children:
            if child.type == "identifier":
                name = self._get_node_text(child, source)
                if name != "self" and name != "cls":
                    params.append(ParameterInfo(name=name))
            elif child.type in ("typed_parameter", "default_parameter", "typed_default_parameter"):
                name_node = self._get_child_by_type(child, "identifier")
                type_node = self._get_child_by_type(child, "type")
                if name_node:
                    name = self._get_node_text(name_node, source)
                    type_hint = self._get_node_text(type_node, source) if type_node else None
                    if name not in ("self", "cls"):
                        params.append(ParameterInfo(name=name, type_hint=type_hint))

        return params

    def _extract_return_type(self, func_node, source: bytes) -> Optional[str]:
        for child in func_node.children:
            if child.type == "type":
                return self._get_node_text(child, source)
        return None

    def _extract_calls(self, func_node, source: bytes) -> List[str]:
        calls = []
        self._collect_calls(func_node, source, calls)
        return list(set(calls))

    def _collect_calls(self, node, source: bytes, calls: list):
        if node.type == "call":
            func_node = node.children[0] if node.children else None
            if func_node:
                if func_node.type == "identifier":
                    calls.append(self._get_node_text(func_node, source))
                elif func_node.type == "attribute":
                    attr = self._get_child_by_type(func_node, "identifier")
                    if attr:
                        calls.append(self._get_node_text(attr, source))
        for child in node.children:
            if child.type == "function_definition":
                continue  # nested function's calls belong to it, not to the outer function
            self._collect_calls(child, source, calls)

    def extract_classes(self, source: bytes, tree) -> List[ClassInfo]:
        classes = []
        for node in tree.root_node.children:
            if node.type == "class_definition":
                cls = self._parse_class(node, source)
                if cls:
                    classes.append(cls)
        return classes

    def _parse_class(self, node, source: bytes) -> Optional[ClassInfo]:
        name_node = self._get_child_by_type(node, "identifier")
        if not name_node:
            return None

        name = self._get_node_text(name_node, source)
        base_classes = self._extract_base_classes(node, source)

        return ClassInfo(
            name=name,
            file_path="",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            base_classes=base_classes,
        )

    def _extract_base_classes(self, class_node, source: bytes) -> List[str]:
        bases = []
        args_node = self._get_child_by_type(class_node, "argument_list")
        if args_node:
            for child in args_node.children:
                if child.type == "identifier":
                    bases.append(self._get_node_text(child, source))
        return bases

    def extract_imports(self, source: bytes, tree) -> List[ImportInfo]:
        imports = []
        for node in tree.root_node.children:
            if node.type == "import_statement":
                raw = self._get_node_text(node, source)
                imports.append(ImportInfo(raw=raw, is_local=False))
            elif node.type == "import_from_statement":
                raw = self._get_node_text(node, source)
                module_node = self._get_child_by_type(node, "dotted_name")
                module = self._get_node_text(module_node, source) if module_node else None
                is_local = raw.startswith("from .") or raw.startswith("from ..")
                imports.append(ImportInfo(raw=raw, module=module, is_local=is_local))
        return imports

    def extract_imported_symbols(self, source: bytes, tree) -> tuple[List[ImportedSymbol], List[ImportedSymbol]]:
        imported_functions = []
        imported_classes = []

        for node in tree.root_node.children:
            if node.type != "import_from_statement":
                continue

            module_node = self._get_child_by_type(node, "dotted_name")
            module = self._get_node_text(module_node, source) if module_node else None

            if any(c.type == "wildcard_import" for c in node.children):
                continue

            import_list = self._get_child_by_type(node, "import_list")
            name_nodes = import_list.children if import_list else node.children

            for child in name_nodes:
                name = None
                alias = None

                if child.type in ("identifier", "dotted_name"):
                    raw_name = self._get_node_text(child, source)
                    # Skip the module name (already captured as first dotted_name child)
                    if module and raw_name == module:
                        continue
                    # For dotted names used as import targets, take the last part
                    name = raw_name.split(".")[-1] if "." in raw_name else raw_name
                elif child.type == "aliased_import":
                    names = [c for c in child.children if c.type in ("identifier", "dotted_name")]
                    if names:
                        raw = self._get_node_text(names[0], source)
                        name = raw.split(".")[-1] if "." in raw else raw
                    if len(names) > 1:
                        alias = self._get_node_text(names[1], source)
                else:
                    continue

                if not name:
                    continue

                is_class = bool(name) and name[0].isalpha() and name[0].isupper()
                symbol = ImportedSymbol(
                    name=name,
                    module=module,
                    alias=alias,
                    is_function=not is_class,
                    is_class=is_class
                )

                if is_class:
                    imported_classes.append(symbol)
                else:
                    imported_functions.append(symbol)

        return imported_functions, imported_classes

    def extract_exported_symbols(self, source: bytes, tree, file_info: FileInfo) -> tuple[List[ExportedSymbol], List[ExportedSymbol]]:
        exported_functions = []
        exported_classes = []

        all_names = self._extract_all_names(source, tree)

        for func in file_info.functions:
            if func.is_method:
                continue
            name = func.name
            if all_names is not None:
                is_exported = name in all_names
            else:
                if name.startswith("__") and name.endswith("__"):
                    is_exported = True
                elif name.startswith("_"):
                    is_exported = False
                else:
                    is_exported = True

            if is_exported:
                exported_functions.append(ExportedSymbol(name=name, type="function", is_public=True))

        for cls in file_info.classes:
            name = cls.name
            if all_names is not None:
                is_exported = name in all_names
            else:
                if name.startswith("__") and name.endswith("__"):
                    is_exported = True
                elif name.startswith("_"):
                    is_exported = False
                else:
                    is_exported = True

            if is_exported:
                exported_classes.append(ExportedSymbol(name=name, type="class", is_public=True))

        return exported_functions, exported_classes

    def _extract_all_names(self, source: bytes, tree) -> Optional[set]:
        for node in tree.root_node.children:
            if node.type == "assignment":
                left = node.children[0] if node.children else None
                if left and left.type == "identifier":
                    if self._get_node_text(left, source) == "__all__":
                        right = node.children[-1] if len(node.children) > 1 else None
                        if right and right.type in ("list", "tuple", "set"):
                            names = set()
                            for child in right.children:
                                if child.type == "string":
                                    val = self._get_node_text(child, source).strip("'\"")
                                    names.add(val)
                                elif child.type == "identifier":
                                    names.add(self._get_node_text(child, source))
                            return names if names else set()
        return None

    def _get_child_by_type(self, node, type_name: str):
        for child in node.children:
            if child.type == type_name:
                return child
        return None

    def _get_child_text(self, node, type_name: str, source: bytes) -> Optional[str]:
        child = self._get_child_by_type(node, type_name)
        return self._get_node_text(child, source) if child else None
