"""Generate RAG gold test cases from Defects4J bugs.

This script automates the slow manual loop:
checkout buggy project -> collect failing test output -> collect modified class/patch
-> infer expected source file/function/line range -> write JSONL dataset rows.

Run from WSL/Linux where the `defects4j` command is on PATH.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_BUGS = "Lang:1,3,5,7,10;Math:1,5,10;Codec:1;Csv:1"


@dataclass(frozen=True)
class BugSpec:
    project: str
    bug_id: int


@dataclass(frozen=True)
class CommandResult:
    command: list[str]
    returncode: int
    stdout: str
    stderr: str


def run_command(command: list[str], cwd: Path | None = None, check: bool = True) -> CommandResult:
    result = subprocess.run(
        command,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    command_result = CommandResult(command, result.returncode, result.stdout, result.stderr)
    if check and result.returncode != 0:
        joined = " ".join(command)
        raise RuntimeError(
            f"Command failed: {joined}\n"
            f"cwd={cwd or Path.cwd()}\n"
            f"stdout:\n{result.stdout}\n"
            f"stderr:\n{result.stderr}"
        )
    return command_result


def parse_bug_specs(raw: str) -> list[BugSpec]:
    specs: list[BugSpec] = []
    for group in raw.split(";"):
        group = group.strip()
        if not group:
            continue
        if ":" not in group:
            raise ValueError(f"Invalid bug group '{group}'. Expected Project:1,2,3")
        project, bug_ids = group.split(":", 1)
        for bug_id in bug_ids.split(","):
            bug_id = bug_id.strip()
            if bug_id:
                specs.append(BugSpec(project=project.strip(), bug_id=int(bug_id)))
    return specs


def clean_ant_output(output: str) -> list[str]:
    values: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("Running ant"):
            continue
        if stripped == "OK":
            continue
        values.append(stripped)
    return values


def export_property(work_dir: Path, property_name: str) -> list[str]:
    result = run_command(["defects4j", "export", "-p", property_name], cwd=work_dir)
    return clean_ant_output(result.stdout)


def checkout_bug(spec: BugSpec, work_root: Path, overwrite: bool) -> Path:
    work_dir = work_root / f"{spec.project.lower()}_{spec.bug_id}_buggy"
    config_file = work_dir / ".defects4j.config"
    if config_file.exists() and not overwrite:
        return work_dir
    if work_dir.exists():
        shutil.rmtree(work_dir)
    run_command(
        ["defects4j", "checkout", "-p", spec.project, "-v", f"{spec.bug_id}b", "-w", str(work_dir)]
    )
    if not config_file.exists():
        raise RuntimeError(f"Checkout did not create {config_file}")
    return work_dir


def read_failing_tests(work_dir: Path) -> str:
    run_command(["defects4j", "test"], cwd=work_dir, check=False)
    failing_file = work_dir / "failing_tests"
    if failing_file.exists():
        return failing_file.read_text(encoding="utf-8", errors="replace").strip()
    return ""


def patch_path(defects4j_root: Path, spec: BugSpec) -> Path:
    src_patch = defects4j_root / "framework" / "projects" / spec.project / "patches" / f"{spec.bug_id}.src.patch"
    if src_patch.exists():
        return src_patch
    raise FileNotFoundError(f"Patch not found: {src_patch}")


def parse_patch(patch_text: str) -> tuple[str, list[int]]:
    expected_file = ""
    expected_lines: list[int] = []
    old_line = 0
    in_hunk = False

    for line in patch_text.splitlines():
        if line.startswith("+++ b/"):
            expected_file = line.removeprefix("+++ b/").strip()
            continue
        hunk_match = re.match(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if hunk_match:
            old_line = int(hunk_match.group(1))
            in_hunk = True
            expected_lines.append(old_line)
            continue
        if not in_hunk:
            continue
        if line.startswith("@@"):
            continue
        if line.startswith("-") and not line.startswith("---"):
            expected_lines.append(old_line)
            old_line += 1
        elif line.startswith("+") and not line.startswith("+++"):
            continue
        else:
            old_line += 1

    unique_lines = sorted(set(expected_lines))
    if len(unique_lines) > 8:
        unique_lines = [unique_lines[0], unique_lines[-1]]
    return expected_file, unique_lines


def class_to_path(source_dir: str, class_name: str) -> str:
    return f"{source_dir.rstrip('/')}/{class_name.replace('.', '/')}.java"


def infer_function_from_java(source_file: Path, expected_lines: list[int]) -> str:
    if not source_file.exists() or not expected_lines:
        return ""
    lines = source_file.read_text(encoding="utf-8", errors="replace").splitlines()
    target_line = min(len(lines), max(expected_lines) + 3)
    method_name_pattern = re.compile(r"\b([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws [^{;]+)?\s*\{?\s*$")
    excluded_starts = ("if ", "for ", "while ", "switch ", "catch ", "return ", "throw ", "new ")
    best = ""
    pending = ""
    for index, line in enumerate(lines, start=1):
        if index > target_line:
            break
        stripped = line.strip()
        if not stripped or stripped.startswith(("//", "*", "/*", "@")):
            continue
        if stripped.startswith(excluded_starts):
            pending = ""
            continue
        pending = f"{pending} {stripped}".strip() if pending else stripped
        if "(" not in pending:
            continue
        if not (pending.endswith("{") or pending.endswith(")") or " throws " in pending):
            continue
        candidate = re.sub(r"\s+", " ", pending).strip()
        declaration_prefix = candidate.split("(", 1)[0].strip()
        prefix_parts = declaration_prefix.split()
        if not prefix_parts or "=" in declaration_prefix or "." in prefix_parts[-1]:
            pending = ""
            continue
        match = method_name_pattern.search(candidate)
        if match:
            before_name = candidate[: match.start(1)].strip()
            if before_name and not before_name.startswith(excluded_starts):
                best = match.group(1)
        pending = ""
    return best

def build_incident_text(spec: BugSpec, trigger_tests: list[str], failing_tests: str) -> str:
    trigger = ", ".join(trigger_tests) if trigger_tests else "unknown trigger test"
    header = f"Defects4J {spec.project}-{spec.bug_id}b failing test: {trigger}."
    if failing_tests:
        return f"{header}\n\n{failing_tests}"
    return header


def build_case(defects4j_root: Path, work_root: Path, spec: BugSpec, overwrite: bool) -> dict[str, object]:
    work_dir = checkout_bug(spec, work_root, overwrite=overwrite)
    source_dirs = export_property(work_dir, "dir.src.classes")
    modified_classes = export_property(work_dir, "classes.modified")
    trigger_tests = export_property(work_dir, "tests.trigger")
    failing_tests = read_failing_tests(work_dir)

    source_dir = source_dirs[0] if source_dirs else "src/main/java"
    patch_file = patch_path(defects4j_root, spec)
    patch_text = patch_file.read_text(encoding="utf-8", errors="replace")
    patch_expected_file, expected_lines = parse_patch(patch_text)

    if patch_expected_file:
        expected_file = patch_expected_file
    elif modified_classes:
        expected_file = class_to_path(source_dir, modified_classes[0])
    else:
        expected_file = ""

    expected_function = infer_function_from_java(work_dir / expected_file, expected_lines)

    return {
        "incident_id": f"D4J-{spec.project.upper()}-{spec.bug_id:03d}",
        "case_type": "defects4j_real_bug",
        "expected_route": "L3",
        "expected_artifact_type": "code",
        "language": "java",
        "defects4j_project": spec.project,
        "defects4j_bug_id": spec.bug_id,
        "repo_dir": str(work_dir),
        "source_dir": source_dir,
        "trigger_tests": trigger_tests,
        "modified_classes": modified_classes,
        "incident_text": build_incident_text(spec, trigger_tests, failing_tests),
        "expected_file": expected_file,
        "expected_function": expected_function,
        "expected_lines": expected_lines,
        "expected_root_cause": "Official Defects4J patch changes this source area; review the linked patch for semantic root cause.",
        "patch_file": str(patch_file),
        "has_traceback": bool(failing_tests),
    }


def write_jsonl(path: Path, rows: Iterable[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate RAG JSONL test cases from Defects4J bugs.")
    parser.add_argument("--defects4j-root", required=True, type=Path, help="Path to the cloned defects4j repo.")
    parser.add_argument("--work-root", required=True, type=Path, help="Directory where buggy versions will be checked out.")
    parser.add_argument("--bugs", default=DEFAULT_BUGS, help="Bug list like 'Lang:1,3;Math:1,5'.")
    parser.add_argument(
        "--output",
        default=Path("rag/evaluation/defects4j_test_events.jsonl"),
        type=Path,
        help="Output JSONL path.",
    )
    parser.add_argument("--overwrite", action="store_true", help="Recreate existing checked-out bug directories.")
    parser.add_argument("--continue-on-error", action="store_true", help="Keep collecting later bugs if one bug fails.")
    args = parser.parse_args()

    defects4j_root = args.defects4j_root.resolve()
    work_root = args.work_root.resolve()
    specs = parse_bug_specs(args.bugs)
    failures_path = args.output.with_suffix(".failures.jsonl")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text("", encoding="utf-8")
    failures_path.write_text("", encoding="utf-8")

    collected = 0
    print(f"[D4J_DATASET] bugs={len(specs)} output={args.output}", flush=True)
    for spec in specs:
        print(f"[D4J_DATASET] collecting project={spec.project} bug={spec.bug_id}", flush=True)
        try:
            row = build_case(defects4j_root, work_root, spec, overwrite=args.overwrite)
            with args.output.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(row, ensure_ascii=True) + "\n")
            collected += 1
            print(
                "[D4J_DATASET] collected "
                f"incident={row['incident_id']} file={row['expected_file']} "
                f"function={row['expected_function']} lines={row['expected_lines']}",
                flush=True,
            )
        except Exception as exc:
            failure = {"project": spec.project, "bug_id": spec.bug_id, "error": str(exc)}
            with failures_path.open("a", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(failure, ensure_ascii=True) + "\n")
            print(f"[D4J_DATASET] failed project={spec.project} bug={spec.bug_id}: {exc}", flush=True)
            if not args.continue_on_error:
                raise

    print(f"[D4J_DATASET] wrote rows={collected} path={args.output}", flush=True)
    if failures_path.stat().st_size:
        print(f"[D4J_DATASET] failures path={failures_path}", flush=True)
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
