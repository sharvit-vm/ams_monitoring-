"""
BaseParser — abstract class all language parsers must implement.
"""

from abc import ABC, abstractmethod
from typing import List
from models import FileInfo, FunctionInfo, ClassInfo, ImportInfo

class BaseParser(ABC):
    """
    Every language parser inherits this.
    Call parse(file_path) to get a fully populated FileInfo.
    """

    @abstractmethod
    def parse(self, file_info: FileInfo) -> FileInfo:
        """
        Main entry point. Reads the file, runs tree-sitter,
        fills in file_info.functions, classes, imports, total_lines.
        Returns the same FileInfo object with fields populated.
        """
        pass

    @abstractmethod
    def extract_functions(self, source: bytes, tree) -> List[FunctionInfo]:
        """Extract all top-level and class-level functions from the AST."""
        pass

    @abstractmethod
    def extract_classes(self, source: bytes, tree) -> List[ClassInfo]:
        """Extract all class definitions from the AST."""
        pass

    @abstractmethod
    def extract_imports(self, source: bytes, tree) -> List[ImportInfo]:
        """Extract all import statements from the AST."""
        pass

    def _get_node_text(self, node, source: bytes) -> str:
        """Helper: get raw source text for any AST node."""
        return source[node.start_byte:node.end_byte].decode("utf-8", errors="ignore")

    def _read_source(self, file_path: str) -> bytes:
        """Helper: read file as bytes for tree-sitter."""
        with open(file_path, "rb") as f:
            return f.read()
