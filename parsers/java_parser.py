"""
Java parser using tree-sitter.
"""
from typing import List, Optional
import tree_sitter_java as tsjava
from tree_sitter import Language, Parser
from models import CallSite, FileInfo, FunctionInfo, ClassInfo, ImportInfo, ParameterInfo, ImportedSymbol, ExportedSymbol
from parsers.base_parser import BaseParser
JAVA_LANGUAGE = Language(tsjava.language())
class JavaParser(BaseParser):
    VERSION = "java-calls-v2"
    def __init__(self):
        self.parser = Parser(JAVA_LANGUAGE)
    def parse(self, file_info: FileInfo) -> FileInfo:
        try:
            source = self._read_source(file_info.absolute_path)
            tree = self.parser.parse(source)
            if tree.root_node.has_error:
                raise ValueError("Java syntax errors prevent reliable call resolution")
            file_info.parser_version = self.VERSION
            file_info.package = self.extract_package(source, tree)
            file_info.classes   = self.extract_classes(source, tree)
            file_info.functions = self.extract_functions(source, tree)
            file_info.imports   = self.extract_imports(source, tree)
            file_info.imported_functions, file_info.imported_classes = self.extract_imported_symbols(source, tree)
            file_info.exported_functions, file_info.exported_classes = self.extract_exported_symbols(file_info)
            class_map = {c.name: c for c in file_info.classes}
            for func in file_info.functions:
                if func.is_method and func.class_name and func.class_name in class_map:
                    class_map[func.class_name].methods.append(func.name)
        except Exception as e:
            file_info.parse_error = str(e)
        return file_info
    def extract_package(self, source: bytes, tree) -> Optional[str]:
        for node in tree.root_node.children:
            if node.type == "package_declaration":
                name_node = self._get_child_by_type(node, "scoped_identifier") or self._get_child_by_type(node, "identifier")
                if name_node:
                    return self._get_node_text(name_node, source)
        return None
    def extract_functions(self, source: bytes, tree) -> List[FunctionInfo]:
        functions = []
        self._walk(tree.root_node, source, functions)
        return functions
    def _walk(self, node, source: bytes, results: list, parent_class: Optional[str] = None):
        if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = self._get_child_by_type(node, "identifier")
            class_name = self._get_node_text(name_node, source) if name_node else parent_class
            body = self._get_child_by_type(node, "class_body") or                   self._get_child_by_type(node, "interface_body")
            if body:
                for child in body.children:
                    if child.type == "method_declaration":
                        func = self._parse_method(child, source, class_name)
                        if func:
                            results.append(func)
                    elif child.type == "constructor_declaration":
                        func = self._parse_method(child, source, class_name, is_constructor=True)
                        if func:
                            results.append(func)
                    else:
                        self._walk(child, source, results, class_name)
        else:
            for child in node.children:
                self._walk(child, source, results, parent_class)
    def _parse_method(self, node, source: bytes, parent_class: Optional[str], is_constructor: bool = False) -> Optional[FunctionInfo]:
        name_node = self._get_child_by_type(node, "identifier")
        if not name_node:
            return None
        name = self._get_node_text(name_node, source)
        params = self._extract_parameters(node, source)
        return_type = self._extract_return_type(node, source) if not is_constructor else parent_class
        calls = self._extract_calls(node, source)
        return FunctionInfo(
            name=name,
            file_path="",
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parameters=params,
            return_type=return_type,
            calls=calls,
            call_sites=self._extract_call_sites(node, source, parent_class),
            is_method=True,
            class_name=parent_class,
        )
    def _extract_parameters(self, method_node, source: bytes) -> List[ParameterInfo]:
        params = []
        params_node = self._get_child_by_type(method_node, "formal_parameters")
        if not params_node:
            return params
        for child in params_node.children:
            if child.type == "spread_parameter":
                params.append(ParameterInfo(name="*args", type_hint="..."))
                continue
            if child.type == "formal_parameter":
                name_node = self._get_child_by_type(child, "identifier")
                type_nodes = [c for c in child.children if c != name_node and c.type not in (",", "(", ")")]
                type_hint = self._get_node_text(type_nodes[0], source) if type_nodes else None
                if name_node:
                    params.append(ParameterInfo(
                        name=self._get_node_text(name_node, source),
                        type_hint=type_hint
                    ))
        return params
    def _extract_return_type(self, method_node, source: bytes) -> Optional[str]:
        for child in method_node.children:
            if child.type in ("type_identifier", "void_type", "integral_type", "floating_point_type",
                              "boolean_type", "array_type", "generic_type"):
                return self._get_node_text(child, source)
        return None
    def _extract_calls(self, node, source: bytes) -> List[str]:
        calls = []
        self._collect_calls(node, source, calls)
        return sorted(set(calls))
    def _collect_calls(self, node, source: bytes, calls: list):
        if node.type == "method_invocation":
            # For receiver.method(args), Tree-sitter exposes both receiver and
            # method identifiers. The last identifier is the invoked method.
            name = node.child_by_field_name("name")
            if name:
                calls.append(self._get_node_text(name, source))
        for child in node.children:
            self._collect_calls(child, source, calls)

    def _extract_call_sites(self, method, source, parent_class):
        text = lambda n: self._get_node_text(n, source) if n else None
        fields = {}
        body = method.parent
        for declaration in body.named_children:
            if declaration.type == "field_declaration":
                type_name = text(declaration.child_by_field_name("type"))
                for variable in declaration.named_children:
                    if variable.type == "variable_declarator":
                        fields[text(variable.child_by_field_name("name"))] = type_name
        scope = dict(fields)
        parameters = method.child_by_field_name("parameters")
        if parameters:
            for parameter in parameters.named_children:
                name = text(parameter.child_by_field_name("name"))
                if name:
                    scope[name] = text(parameter.child_by_field_name("type"))
        sites = []

        def walk(node, bindings):
            # Nested declarations have their own receiver scope.
            if node.type in ("class_declaration", "interface_declaration", "class_body"):
                return
            local = dict(bindings)
            if node.type == "catch_clause":
                parameter = next((c for c in node.named_children if c.type == "catch_formal_parameter"), None)
                if parameter:
                    local[text(parameter.child_by_field_name("name"))] = None
            if node.type == "lambda_expression":
                params = node.child_by_field_name("parameters")
                if params:
                    names = [params] if params.type == "identifier" else params.named_children
                    for param in names:
                        name = text(param) if param.type == "identifier" else text(param.child_by_field_name("name"))
                        local[name] = None
            if node.type in ("catch_formal_parameter", "enhanced_for_statement"):
                name = text(node.child_by_field_name("name"))
                if name:
                    local[name] = text(node.child_by_field_name("type"))
            if node.type == "method_invocation":
                receiver = node.child_by_field_name("object")
                receiver_text = text(receiver)
                receiver_type = None
                if receiver is None or receiver_text == "this":
                    receiver_type = parent_class
                elif receiver.type == "identifier":
                    # Unknown lowercase identifiers cannot be assumed to be types.
                    receiver_type = local.get(receiver_text)
                    if receiver_text not in local and receiver_text[:1].isupper():
                        receiver_type = receiver_text
                elif receiver.type == "field_access" and text(receiver.child_by_field_name("object")) == "this":
                    receiver_type = fields.get(text(receiver.child_by_field_name("field")))
                elif receiver.type == "object_creation_expression":
                    receiver_type = text(receiver.child_by_field_name("type"))
                arguments = node.child_by_field_name("arguments")
                sites.append(CallSite(
                    name=text(node.child_by_field_name("name")), receiver=receiver_text,
                    receiver_type=receiver_type, line=node.start_point[0] + 1,
                    argument_count=len(arguments.named_children) if arguments else 0,
                ))
            for child in node.named_children:
                if child.type == "local_variable_declaration":
                    declared_type = text(child.child_by_field_name("type"))
                    for variable in child.named_children:
                        if variable.type == "variable_declarator":
                            local[text(variable.child_by_field_name("name"))] = declared_type
                walk(child, local)

        walk(method, scope)
        return sites
    def extract_classes(self, source: bytes, tree) -> List[ClassInfo]:
        classes = []
        self._walk_classes(tree.root_node, source, classes)
        return classes
    def _walk_classes(self, node, source: bytes, results: list):
        if node.type in ("class_declaration", "interface_declaration", "enum_declaration"):
            name_node = self._get_child_by_type(node, "identifier")
            if name_node:
                base_classes, implemented_interfaces, extended_classes = self._extract_inheritance(node, source)
                results.append(ClassInfo(
                    name=self._get_node_text(name_node, source),
                    file_path="",
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    base_classes=base_classes,
                    implemented_interfaces=implemented_interfaces,
                    extended_classes=extended_classes,
                    is_interface=node.type == "interface_declaration",
                ))
        for child in node.children:
            self._walk_classes(child, source, results)
    def _extract_superclass(self, class_node, source: bytes) -> List[str]:
        base_classes, _, _ = self._extract_inheritance(class_node, source)
        return base_classes

    def _extract_inheritance(self, class_node, source: bytes) -> tuple[List[str], List[str], List[str]]:
        bases = []
        implemented_interfaces = []
        extended_classes = []
        for child in class_node.children:
            if child.type == "superclass":
                names = self._type_names(child, source)
                bases.extend(names)
                extended_classes.extend(names)
            elif child.type in ("super_interfaces", "extends_interfaces"):
                names = self._type_names(child, source)
                bases.extend(names)
                if child.type == "extends_interfaces":
                    extended_classes.extend(names)
                else:
                    implemented_interfaces.extend(names)
        return bases, implemented_interfaces, extended_classes

    def _type_names(self, node, source: bytes) -> List[str]:
        """Extract type identifiers from direct or nested Java type lists."""
        names = []

        def walk(current):
            if current.type == "generic_type":
                walk(current.named_children[0])
                return
            if current.type in ("type_identifier", "scoped_type_identifier"):
                names.append(self._get_node_text(current, source))
                return
            for child in current.children:
                walk(child)

        walk(node)
        return names
    def extract_imports(self, source: bytes, tree) -> List[ImportInfo]:
        imports = []
        for node in tree.root_node.children:
            if node.type == "import_declaration":
                raw = self._get_node_text(node, source)
                name_node = self._get_child_by_type(node, "scoped_identifier") or                            self._get_child_by_type(node, "identifier")
                module = self._get_node_text(name_node, source) if name_node else None
                imports.append(ImportInfo(raw=raw.strip(), module=module))
        return imports
    def extract_imported_symbols(self, source: bytes, tree) -> tuple[List[ImportedSymbol], List[ImportedSymbol]]:
        imported_functions = []
        imported_classes = []
        for node in tree.root_node.children:
            if node.type == "import_declaration":
                raw = self._get_node_text(node, source)
                is_static = "static" in raw
                name_node = self._get_child_by_type(node, "scoped_identifier") or                           self._get_child_by_type(node, "identifier")
                if name_node:
                    full_name = self._get_node_text(name_node, source)
                    parts = full_name.split(".") if full_name else []
                    if is_static and len(parts) >= 2:
                        class_name = parts[-2] if len(parts) >= 2 else ""
                        method_name = parts[-1] if parts else ""
                        if method_name and method_name[0].islower():
                            imported_functions.append(ImportedSymbol(
                                name=method_name,
                                module=".".join(parts[:-1]),
                                is_function=True,
                                is_class=False
                            ))
                        elif method_name and method_name[0].isupper():
                            imported_classes.append(ImportedSymbol(
                                name=method_name,
                                module=".".join(parts[:-1]),
                                is_function=False,
                                is_class=True
                            ))
                    elif len(parts) >= 1:
                        class_name = parts[-1]
                        if class_name and class_name[0].isupper():
                            imported_classes.append(ImportedSymbol(
                                name=class_name,
                                module=".".join(parts[:-1]) if len(parts) > 1 else None,
                                is_function=False,
                                is_class=True
                            ))
        return imported_functions, imported_classes
    def extract_exported_symbols(self, file_info: FileInfo) -> tuple[List[ExportedSymbol], List[ExportedSymbol]]:
        exported_functions = []
        exported_classes = []
        for func in file_info.functions:
            if func.is_method:
                exported_functions.append(ExportedSymbol(
                    name=func.name,
                    type="function",
                    is_public=True
                ))
        for cls in file_info.classes:
            exported_classes.append(ExportedSymbol(
                name=cls.name,
                type="class",
                is_public=True
            ))
        return exported_functions, exported_classes
    def _get_child_by_type(self, node, type_name: str):
        for child in node.children:
            if child.type == type_name:
                return child
        return None
