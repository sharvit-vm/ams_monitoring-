import re


TRACEBACK_RE = re.compile(r"(Traceback \(most recent call last\):.*?)(?=\n\n|\Z)", re.DOTALL)
EXCEPTION_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*(?:Error|Exception|Warning)):\s*(.+)$", re.MULTILINE)
FILE_LINE_RE = re.compile(r'File "([^"]+)", line (\d+), in (\S+)')
JAVA_STACK_RE = re.compile(
    r"((?:[A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:Error|Exception))(?::[^\n]*)?\n"
    r"(?:\s+at\s+[\w.$<>]+\([^()\n]+\.java:\d+\)\n?)+)",
    re.MULTILINE,
)
JAVA_EXCEPTION_RE = re.compile(
    r"^([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)*(?:Error|Exception))(?::\s*(.*))?$",
    re.MULTILINE,
)
JAVA_FRAME_RE = re.compile(r"\s+at\s+([\w.$]+)\.([\w$<>]+)\(([^():]+\.java):(\d+)\)")
CODE_FENCE_RE = re.compile(r"```(?:\w+)?\n(.*?)```", re.DOTALL)


def extract_traceback(text: str) -> str:
    for fence in CODE_FENCE_RE.finditer(text):
        fenced_text = fence.group(1)
        match = TRACEBACK_RE.search(fenced_text) or JAVA_STACK_RE.search(fenced_text)
        if match:
            return match.group(1).strip()
    match = TRACEBACK_RE.search(text) or JAVA_STACK_RE.search(text)
    return match.group(1).strip() if match else ""


def extract_error_type_message(tb: str, title: str) -> tuple[str, str]:
    if tb:
        matches = list(EXCEPTION_RE.finditer(tb))
        if matches:
            last = matches[-1]
            return last.group(1), last.group(2).strip()
        java_matches = list(JAVA_EXCEPTION_RE.finditer(tb))
        if java_matches:
            last = java_matches[-1]
            return last.group(1), (last.group(2) or "").strip()
    match = EXCEPTION_RE.match(title.strip())
    if match:
        return match.group(1), match.group(2).strip()
    match = JAVA_EXCEPTION_RE.match(title.strip())
    if match:
        return match.group(1), (match.group(2) or "").strip()
    return "Error", title.strip()


def extract_file_info(tb: str) -> tuple[str, int, str]:
    if not tb:
        return "", 0, ""
    matches = list(FILE_LINE_RE.finditer(tb))
    if matches:
        last = matches[-1]
        return last.group(1), int(last.group(2)), last.group(3)
    java_matches = list(JAVA_FRAME_RE.finditer(tb))
    if java_matches:
        first = java_matches[0]
        return first.group(3), int(first.group(4)), first.group(2)
    return "", 0, ""


def extract_java_class_name(tb: str) -> str:
    java_matches = list(JAVA_FRAME_RE.finditer(tb))
    if not java_matches:
        return ""
    return java_matches[0].group(1)
