from pathlib import Path
from typing import Optional

EXTENSION_MAP: dict[str, str] = {

    ".py": "python",

    ".js": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",

    ".ts": "typescript",
    ".tsx": "typescript",

    ".go": "go",

    ".java": "java",

    ".rs": "rust",

    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".hpp": "cpp",

    ".rb": "ruby",

    ".php": "php",

    ".cs": "csharp",

    ".kt": "kotlin",
    ".kts": "kotlin",

    ".swift": "swift",

    ".scala": "scala",

    ".sh": "shell",
    ".bash": "shell",

    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",
    ".env": "env",

    ".md": "markdown",
    ".mdx": "markdown",

    ".sql": "sql",

    ".html": "html",
    ".htm": "html",
    ".css": "css",
    ".scss": "scss",
}

PARSEABLE_LANGUAGES = {
    "python",
    "javascript",
    "typescript",
    "go",
    "java",
}

IGNORED_DIRS = {
    ".git",
    ".github",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".env",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    "coverage",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "target",
    "vendor",
    ".idea",
    ".vscode",
    "*.egg-info",
}

IGNORED_EXTENSIONS = {
    ".pyc",
    ".pyo",
    ".pyd",
    ".so",
    ".dll",
    ".exe",
    ".bin",
    ".lock",
    ".log",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".mp4",
    ".mp3",
    ".zip",
    ".tar",
    ".gz",
    ".pdf",
    ".DS_Store",
}

def detect_language(file_path: str) -> Optional[str]:
    """
    Returns the language string for a given file path based on its extension.
    Returns None if the file type is unknown or should be ignored.
    """
    ext = Path(file_path).suffix.lower()
    if ext in IGNORED_EXTENSIONS:
        return None
    return EXTENSION_MAP.get(ext, None)

def is_parseable(language: Optional[str]) -> bool:
    """
    Returns True if we have a tree-sitter parser for this language.
    Files with unparseable languages are still stored in Neo4j as FileNodes
    but without function/class extraction.
    """
    if language is None:
        return False
    return language in PARSEABLE_LANGUAGES

def should_skip_dir(dir_name: str) -> bool:
    """
    Returns True if a directory should be skipped entirely during scanning.
    """
    return dir_name in IGNORED_DIRS or dir_name.startswith(".")

def get_language_display_name(language: str) -> str:
    """
    Returns a human-readable display name for a language code.
    Useful for LLM prompts.
    """
    display_names = {
        "python": "Python",
        "javascript": "JavaScript",
        "typescript": "TypeScript",
        "go": "Go",
        "java": "Java",
        "rust": "Rust",
        "c": "C",
        "cpp": "C++",
        "csharp": "C#",
        "ruby": "Ruby",
        "php": "PHP",
        "kotlin": "Kotlin",
        "swift": "Swift",
        "scala": "Scala",
        "shell": "Shell",
        "yaml": "YAML",
        "json": "JSON",
        "sql": "SQL",
        "html": "HTML",
        "css": "CSS",
        "markdown": "Markdown",
    }
    return display_names.get(language, language.capitalize())

if __name__ == "__main__":

    test_files = [
        "src/main.py",
        "src/index.ts",
        "app/handler.go",
        "Service.java",
        "README.md",
        "config.yaml",
        "image.png",
        "unknown.xyz",
    ]
    print("Language detection test:")
    for f in test_files:
        lang = detect_language(f)
        parseable = is_parseable(lang)
        print(f"  {f:<30} → {str(lang):<15} parseable={parseable}")
