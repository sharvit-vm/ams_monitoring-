"""
Code Fix Agent

Receives an ErrorEvent + RCAResult from the RCA agent, writes the actual
code fix to the cloned repo, then opens a GitHub PR that closes the issue.

Flow:
  RCAResult (what to fix)
      ->
  Code Fix Agent reads the buggy file/function
      ->
  Generates the patched code using write_fix tool
      ->
  Commits + pushes to a new branch
      ->
  Opens GitHub PR that auto-closes the issue
"""

import os
import json
import subprocess
import time
from typing import List, Optional
from urllib.parse import urlparse, urlunparse
from pydantic import BaseModel, Field
from langchain_core.messages import HumanMessage
from langchain.agents import create_agent
from github import Github, GithubException

from config import agent_llm
from issuelayer.intake.schemas import ErrorEvent
from agents.rca import RCAResult
from agents.skill_loader import build_skill_prompt
from tools.file_tool import read_file, read_file_range, write_fix, get_token_count, reset_tool_context, set_tool_context
from tools.neo4j_tool import get_connected_files, get_function_calls, get_file_summary
from observability.agent_trace import log_agent_event, make_evidence_record, trace_span
from observability.token_usage import track_usage, usage_config
from governance.approvals import RemediationPlan, approval_store
from governance.telemetry import emit_governance_event


CLONE_DIR    = os.getenv("CLONE_DIR", "clone")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")


# -- Result model --------------------------------------------------------------

class CodeFixResult(BaseModel):
    success: bool
    status: str = ""
    approval_id: Optional[str] = None
    remediation_plan: Optional[dict] = None
    pr_url: Optional[str] = None
    pr_number: Optional[int] = None
    branch_name: str = ""
    files_changed: List[str] = []
    patch_summary: str = ""
    confidence: str = ""
    verification_summary: str = ""
    verification_records: List[dict] = []
    error: Optional[str] = None
    token_usage: dict = Field(default_factory=dict)


# -- Git helpers ---------------------------------------------------------------

def _git(args: list, cwd: str) -> tuple:
    result = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if GITHUB_TOKEN:
        stdout = stdout.replace(GITHUB_TOKEN, "***")
        stderr = stderr.replace(GITHUB_TOKEN, "***")
    return result.returncode, stdout, stderr


def _repo_url_with_token(repo_url: str) -> str:
    if not GITHUB_TOKEN or not repo_url.startswith("https://"):
        return repo_url
    parsed = urlparse(repo_url)
    if "github.com" not in parsed.netloc:
        return repo_url
    netloc = f"x-access-token:{GITHUB_TOKEN}@{parsed.netloc}"
    return urlunparse(parsed._replace(netloc=netloc))


def _prepare_fix_branch(event_id: str, repo_dir: str) -> str:
    branch_name = f"codefix/{event_id[:8]}"
    rc, _, err = _git(["checkout", "-b", branch_name], cwd=repo_dir)
    if rc != 0:
        rc2, _, _ = _git(["checkout", branch_name], cwd=repo_dir)
        if rc2 != 0:
            raise RuntimeError(f"Could not create or checkout branch {branch_name}: {err}")
    return branch_name


def _commit_and_push(branch_name: str, commit_msg: str, repo_dir: str, repo_url: str) -> list:
    _git(["config", "user.name", "CodeFix Bot"], cwd=repo_dir)
    _git(["config", "user.email", "codefix-bot@users.noreply.github.com"], cwd=repo_dir)

    _git(["add", "-A"], cwd=repo_dir)
    _, diff_out, _ = _git(["diff", "--cached", "--name-only"], cwd=repo_dir)
    changed_files = [f for f in diff_out.splitlines() if f]

    if not changed_files:
        raise RuntimeError("No files changed - fix was not applied")

    rc, _, err = _git(["commit", "-m", commit_msg], cwd=repo_dir)
    if rc != 0:
        raise RuntimeError(f"Commit failed: {err}")

    push_target = _repo_url_with_token(repo_url)
    rc, _, err = _git(["push", push_target, branch_name], cwd=repo_dir)
    if rc != 0:
        raise RuntimeError(f"Push failed: {err}")

    return changed_files


def _changed_files(repo_dir: str) -> list[str]:
    _, diff_out, _ = _git(["diff", "--name-only"], cwd=repo_dir)
    return [f for f in diff_out.splitlines() if f]


def _normalize_repo_path(path: str | None) -> str:
    return (path or "").replace("\\", "/").strip().lstrip("./")


def _allowed_fix_files(rca: RCAResult) -> set[str]:
    # Automatic codefix is intentionally constrained to the primary RCA file.
    # Related/affected files are evidence for reasoning, not automatic edit targets.
    buggy_file = _normalize_repo_path(getattr(rca, "buggy_file", ""))
    return {buggy_file} if buggy_file else set()


def _validate_patch_scope(
    rca: RCAResult,
    changed_files: list[str],
    agent_files_changed: list[str] | None = None,
) -> tuple[bool, str, list[str]]:
    allowed_files = _allowed_fix_files(rca)
    normalized_changed = [_normalize_repo_path(path) for path in changed_files if _normalize_repo_path(path)]
    normalized_agent_files = [_normalize_repo_path(path) for path in (agent_files_changed or []) if _normalize_repo_path(path)]

    if not allowed_files:
        return False, "RCA did not provide a buggy_file, so no safe automatic edit target exists.", sorted(allowed_files)
    if not normalized_changed:
        return False, "No files were changed by the patch agent.", sorted(allowed_files)

    changed_set = set(normalized_changed)
    unexpected = sorted(changed_set - allowed_files)
    missing_required = sorted(allowed_files - changed_set)
    if unexpected:
        return False, f"Patch touched files outside RCA boundary: {', '.join(unexpected)}.", sorted(allowed_files)
    if missing_required:
        return False, f"Patch did not modify the RCA buggy_file: {', '.join(missing_required)}.", sorted(allowed_files)

    if normalized_agent_files:
        agent_set = set(normalized_agent_files)
        if agent_set != changed_set:
            return False, "Patch summary files_changed does not match the actual git diff.", sorted(allowed_files)

    return True, "Patch scope matches RCA buggy_file boundary.", sorted(allowed_files)


def _discard_worktree_changes(repo_dir: str) -> None:
    rc, _, _ = _git(["restore", "."], cwd=repo_dir)
    if rc != 0:
        _git(["checkout", "--", "."], cwd=repo_dir)


def _open_pr(repo_full_name, branch_name, base_branch, event, rca, changed_files):
    g = Github(GITHUB_TOKEN)
    repo = g.get_repo(repo_full_name)

    issue_ref = ""
    if event.source == "github_issue" and event.external_number:
        issue_ref = f"Closes #{event.external_number}"
    title = f"fix({rca.buggy_function}): {rca.root_cause[:72]}"

    body = f"""## Automated Code Fix

**Issue:** {event.error_type} - {event.message}
**Confidence:** {rca.confidence}

### Root Cause
{rca.root_cause}

### Files Changed
{chr(10).join(f'- `{f}`' for f in changed_files)}

### Reasoning
{rca.reasoning}

---
{issue_ref}

> Generated by CodeFix MultiAgent Workflow
"""

    pr = repo.create_pull(
        title=title,
        body=body,
        head=branch_name,
        base=base_branch,
    )
    return pr.html_url, pr.number


# -- System prompt -------------------------------------------------------------

CODE_FIX_SKILL_PROMPT = ""


CODE_FIX_PROMPT = """You are an expert software engineer. You MUST write a code fix by calling the write_fix tool.

Use the loaded codefix skill as the task-specific patch safety playbook.

{CODE_FIX_SKILL_PROMPT}

YOU MUST CALL write_fix - do not just reason about the fix, actually apply it.

Steps you MUST follow in order:
1. Call read_file with the buggy_file path to read the source code
2. Identify the exact lines that need to change based on the RCA fix_suggestion
3. Call write_fix with the corrected code - THIS STEP IS MANDATORY
4. Call read_file_range to verify your fix looks correct
5. Output a JSON summary

The write_fix tool signature:
  file_path  : path to the file  (e.g. "rag_agent/retriever.py")
  start_line : first line number to replace
  end_line   : last line number to replace
  new_code   : the replacement code (preserve indentation exactly)

Rules:
- Make the SMALLEST possible change that fixes the bug
- Do NOT refactor beyond the fix
- Preserve all indentation and style exactly

After calling write_fix, output ONLY this JSON - no other text:
{
  "files_changed": ["path/to/file.py"],
  "patch_summary": "one sentence: what you changed and why",
  "lines_changed": [[start_line, end_line]]
}"""


# -- Main entry point ----------------------------------------------------------

CODE_FIX_PROMPT = f"""You are an expert software engineer. Your primary job is to write a code fix by calling the write_fix tool when the RCA gives enough evidence for a safe patch.

Use the loaded codefix skill as the task-specific patch safety playbook.

{CODE_FIX_SKILL_PROMPT}

If the RCA is low-confidence or the target code path cannot be located, do not invent a speculative edit. Otherwise, call write_fix and actually apply the fix.

Steps you MUST follow in order:
1. Call read_file with the buggy_file path to read the source code
2. Identify the exact lines that need to change based on the RCA fix_suggestion
3. Call write_fix with the corrected code when the change is evidence-backed
4. Call read_file_range to verify your fix looks correct after editing
5. Output a JSON summary

The write_fix tool signature:
  file_path  : path to the file  (e.g. "rag_agent/retriever.py")
  start_line : first line number to replace
  end_line   : last line number to replace
  new_code   : the replacement code (preserve indentation exactly)

Rules:
- Make the SMALLEST possible change that fixes the bug
- Do NOT refactor beyond the fix
- Preserve all indentation and style exactly
- For missing/null/undefined input bugs, the guard or default must run before the first unsafe use/dereference
- Replace or move the unsafe statement; do not leave the original crashing line before the guard
- Do not introduce duplicate declarations, duplicate assignments, unreachable code, or two competing versions of the same logic in one scope
- After read_file_range, verify the original failing input path cannot still hit the old crash point
- If verification shows the patch is still wrong, call write_fix again before producing the final JSON

After calling write_fix, output ONLY this JSON:
{{
  "files_changed": ["path/to/file.py"],
  "patch_summary": "one sentence: what you changed and why",
  "lines_changed": [[start_line, end_line]]
}}

If you could not safely apply a patch, output ONLY this JSON:
{{
  "files_changed": [],
  "patch_summary": "no safe patch applied: concise blocker",
  "lines_changed": []
}}

No extra text before or after the JSON."""


def build_code_fix_system_prompt() -> str:
    code_fix_skill_prompt = build_skill_prompt("codefix")
    return f"""You are an expert software engineer. Your primary job is to write a code fix by calling the write_fix tool when the RCA gives enough evidence for a safe patch.

Use the loaded codefix skill as the task-specific patch safety playbook.

{code_fix_skill_prompt}

If the RCA is low-confidence or the target code path cannot be located, do not invent a speculative edit. Otherwise, call write_fix and actually apply the fix.

Steps you MUST follow in order:
1. Call read_file with the buggy_file path to read the source code
2. Identify the exact lines that need to change based on the RCA fix_suggestion
3. Call write_fix with the corrected code when the change is evidence-backed
4. Call read_file_range to verify your fix looks correct after editing
5. Output a JSON summary

The write_fix tool signature:
  file_path  : path to the file  (e.g. "rag_agent/retriever.py")
  start_line : first line number to replace
  end_line   : last line number to replace
  new_code   : the replacement code (preserve indentation exactly)

Rules:
- Make the SMALLEST possible change that fixes the bug
- Do NOT refactor beyond the fix
- Preserve all indentation and style exactly
- You may only call write_fix for the strict Allowed edit files listed in the user message
- Do not patch caller/controller/related files unless they are explicitly listed as allowed edit files

After calling write_fix, output ONLY this JSON:
{{
  "files_changed": ["path/to/file.py"],
  "patch_summary": "one sentence: what you changed and why",
  "lines_changed": [[start_line, end_line]]
}}

If you could not safely apply a patch, output ONLY this JSON:
{{
  "files_changed": [],
  "patch_summary": "no safe patch applied: concise blocker",
  "lines_changed": []
}}

No extra text before or after the JSON."""


@track_usage
def run_code_fix(
    event: ErrorEvent,
    rca: RCAResult,
    knowledge_id: str,
    repo_dir: str = CLONE_DIR,
    require_approval: bool = True,
) -> CodeFixResult:
    started_at = time.perf_counter()
    incident_id = event.incident_id or event.external_id or ""
    rca_confidence = getattr(rca, "confidence", "")
    allowed_files = sorted(_allowed_fix_files(rca))
    verification_records: list[dict] = []

    log_agent_event(
        agent="codefix",
        stage="started",
        status="running",
        event_id=event.id,
        incident_id=incident_id,
        repo=event.repo_full_name,
        file=getattr(rca, "buggy_file", event.file_path),
        confidence=rca_confidence,
    )

    if not GITHUB_TOKEN:
        log_agent_event(
            agent="codefix",
            stage="precheck",
            status="failed",
            event_id=event.id,
            incident_id=incident_id,
            summary="GITHUB_TOKEN is missing; cannot push branch or open PR.",
        )
        return CodeFixResult(
            success=False,
            error="GITHUB_TOKEN not set - cannot push or open PR",
            branch_name="",
        )

    if not os.path.isdir(repo_dir):
        log_agent_event(
            agent="codefix",
            stage="precheck",
            status="failed",
            event_id=event.id,
            incident_id=incident_id,
            summary=f"Clone directory not found: {repo_dir}.",
        )
        return CodeFixResult(
            success=False,
            error=f"Clone directory not found: {repo_dir}. Repository checkout failed.",
            branch_name="",
        )

    if str(rca_confidence).lower() == "low":
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_001",
            evidence_type="policy_gate",
            tool="codefix_precheck",
            status="blocked",
            summary="Automatic code fix skipped because RCA confidence is low.",
            confidence_impact="low",
        ))
        log_agent_event(
            agent="codefix",
            stage="precheck",
            status="blocked",
            event_id=event.id,
            incident_id=incident_id,
            summary="RCA confidence is low, so no automatic patch will be created.",
        )
        return CodeFixResult(
            success=False,
            error="RCA confidence is low; automatic code fix skipped.",
            branch_name="",
            confidence=rca_confidence,
            verification_summary="Blocked by low-confidence RCA policy.",
            verification_records=verification_records,
        )

    if require_approval:
        confidence_map = {"low": 0.25, "medium": 0.65, "high": 0.9}
        confidence_score = confidence_map.get(str(rca_confidence).lower(), 0.5)
        plan = approval_store.create(RemediationPlan(
            agent_type="code_fix",
            target_type="code_repository",
            source_platform=(event.source or "").replace("_issue", "") or "github",
            issue_id=event.incident_id or event.external_id or event.id,
            issue_summary=f"{event.error_type}: {event.message}",
            severity=str(event.priority or "medium"),
            confidence=confidence_score,
            recommended_action=rca.fix_suggestion or f"Patch {rca.buggy_file}",
            expected_impact=f"Create a fix branch and pull request touching {rca.buggy_file}.",
            estimated_execution_time="3-5 minutes",
            risk_level="medium" if confidence_score >= 0.65 else "high",
            evidence=[
                {"type": "traceback", "value": event.traceback},
                {"type": "root_cause", "value": rca.root_cause},
                {"type": "buggy_file", "value": rca.buggy_file},
                {"type": "buggy_lines", "value": rca.buggy_lines},
                {"type": "affected_files", "value": rca.affected_files},
                {"type": "allowed_edit_files", "value": allowed_files},
            ],
            plan=[
                {"step": "prepare_branch", "target": event.repo_full_name},
                {"step": "generate_patch", "target": rca.buggy_file},
                {"step": "verify_diff", "target": repo_dir},
                {"step": "push_branch_and_open_pr", "target": event.repo_full_name},
            ],
            execution_context={
                "event": event.model_dump(mode="json"),
                "rca": rca.model_dump() if hasattr(rca, "model_dump") else dict(rca),
                "knowledge_id": knowledge_id,
                "repo_dir": repo_dir,
            },
        ))
        emit_governance_event(
            "remediation.waiting_for_approval",
            approval_id=plan.approval_id,
            agent_type="code_fix",
            issue_id=plan.issue_id,
        )
        return CodeFixResult(
            success=False,
            status="WAITING_FOR_APPROVAL",
            approval_id=plan.approval_id,
            remediation_plan=plan.model_dump(),
            confidence=rca_confidence,
            verification_summary="Code remediation plan is waiting for human approval.",
        )

    # Step 1 - Create fix branch

    try:
        branch_name = _prepare_fix_branch(event.id, repo_dir)
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_001",
            evidence_type="git",
            tool="git_checkout_branch",
            status="passed",
            summary=f"Prepared fix branch {branch_name}.",
            confidence_impact="neutral",
        ))
        log_agent_event(
            agent="codefix",
            stage="branch_prepared",
            status="completed",
            event_id=event.id,
            incident_id=incident_id,
            branch=branch_name,
        )
    except Exception as e:
        log_agent_event(
            agent="codefix",
            stage="branch_prepared",
            status="failed",
            event_id=event.id,
            incident_id=incident_id,
            summary=str(e),
        )
        return CodeFixResult(
            success=False,
            error=f"Branch creation failed: {e}",
            branch_name="",
            confidence=rca_confidence,
            verification_summary="Branch creation failed.",
            verification_records=verification_records,
        )

    pre_existing_changes = _changed_files(repo_dir)
    if pre_existing_changes:
        log_agent_event(
            agent="codefix",
            stage="precheck",
            status="blocked",
            event_id=event.id,
            incident_id=incident_id,
            summary="Repository checkout has pre-existing uncommitted changes; refusing to generate an automated patch.",
            changed_files=pre_existing_changes,
        )
        return CodeFixResult(
            success=False,
            error="Repository checkout is dirty before codefix; automatic patch skipped.",
            branch_name=branch_name,
            confidence=rca_confidence,
            verification_summary="Blocked because checkout had pre-existing uncommitted changes.",
            verification_records=verification_records,
        )

    # Step 2 - Run the code fix agent
    agent = create_agent(
        model=agent_llm,
        tools=[read_file, read_file_range, write_fix, get_token_count,
               get_function_calls, get_file_summary],
        system_prompt=build_code_fix_system_prompt(),
    )

    user_message = f"""
Bug Report:
  Error type    : {event.error_type}
  Message       : {event.message}
  File          : {event.file_path}
  Line          : {event.line_number}
  Function      : {event.function_name}
  Knowledge ID  : {knowledge_id}

Traceback:
{event.traceback}

RCA Result:
  Root cause    : {rca.root_cause}
  Buggy file    : {rca.buggy_file}
  Buggy function: {rca.buggy_function}
  Buggy lines   : {rca.buggy_lines}
  Affected files: {rca.affected_files}
  Allowed edit files STRICT: {allowed_files}
  Fix suggestion: {rca.fix_suggestion}
  Confidence    : {rca.confidence}

Apply the fix and return your JSON summary. If the safest edit seems to be outside Allowed edit files, do not patch; return files_changed as an empty list with the blocker in patch_summary.
"""

    patch_summary = ""
    agent_output: dict = {}
    try:
        log_agent_event(
            agent="codefix",
            stage="llm_patch_generation",
            status="running",
            event_id=event.id,
            incident_id=incident_id,
            summary="Agent will read source, apply write_fix, and verify the changed range.",
            tools=["read_file", "write_fix", "read_file_range", "get_function_calls", "get_file_summary"],
        )
        repo_token, knowledge_token = set_tool_context(repo_dir, knowledge_id)
        try:
            with trace_span(
                name="codefix.llm_patch_generation",
                event_id=event.id,
                incident_id=incident_id,
                agent="codefix",
                input_data={
                    "buggy_file": rca.buggy_file,
                    "buggy_lines": rca.buggy_lines,
                    "confidence": rca_confidence,
                    "fix_suggestion": rca.fix_suggestion,
                },
                metadata={"repo": event.repo_full_name, "knowledge_id": knowledge_id},
            ):
                result = agent.invoke({
                    "messages": [HumanMessage(content=user_message)]
                }, config=usage_config())
        finally:
            reset_tool_context(repo_token, knowledge_token)

        last_message = result["messages"][-1].content
        clean = last_message.strip()
        if clean.startswith("```"):
            clean = clean.split("```")[1]
            if clean.startswith("json"):
                clean = clean[4:]
            clean = clean.strip()

        agent_output = json.loads(clean)
        patch_summary = agent_output.get("patch_summary", "Code fix applied")
        agent_files_changed = agent_output.get("files_changed") or []
        if not isinstance(agent_files_changed, list):
            raise ValueError("Patch summary files_changed must be a list")
        if not agent_files_changed:
            raise ValueError(patch_summary or "Patch agent did not apply a safe edit")
        scope_ok, scope_reason, scope_allowed = _validate_patch_scope(rca, _changed_files(repo_dir), agent_files_changed)
        if not scope_ok:
            raise ValueError(f"Unsafe patch scope: {scope_reason} Allowed files: {scope_allowed}")
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_002",
            evidence_type="patch",
            tool="write_fix",
            status="passed" if agent_output.get("files_changed") else "unknown",
            file_path=(agent_output.get("files_changed") or [rca.buggy_file])[0],
            summary=patch_summary,
            confidence_impact="high",
            metadata={
                "lines_changed": agent_output.get("lines_changed") or [],
                "agent_files_changed": agent_output.get("files_changed") or [],
            },
        ))
        log_agent_event(
            agent="codefix",
            stage="patch_generated",
            status="completed",
            event_id=event.id,
            incident_id=incident_id,
            summary=patch_summary,
            files_changed=agent_output.get("files_changed") or [],
            lines_changed=agent_output.get("lines_changed") or [],
        )

    except Exception as e:
        _discard_worktree_changes(repo_dir)
        patch_summary = f"No safe patch applied: {e}"
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_002",
            evidence_type="patch",
            tool="codefix_agent",
            status="blocked",
            file_path=rca.buggy_file,
            summary=f"Agent invocation, JSON parsing, or scope validation blocked the patch: {e}",
            confidence_impact="low",
        ))
        log_agent_event(
            agent="codefix",
            stage="patch_generated",
            status="blocked",
            event_id=event.id,
            incident_id=incident_id,
            summary=f"Patch generation blocked: {e}",
        )
        return CodeFixResult(
            success=False,
            error=f"Patch generation blocked: {e}",
            branch_name=branch_name,
            confidence=rca_confidence,
            verification_summary="Blocked because patch output was invalid or outside RCA file boundary.",
            verification_records=verification_records,
        )

    # Step 3 - Commit and push
    changed_before_commit = _changed_files(repo_dir)
    scope_ok, scope_reason, scope_allowed = _validate_patch_scope(rca, changed_before_commit, agent_output.get("files_changed") or [])
    if not scope_ok:
        _discard_worktree_changes(repo_dir)
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_003",
            evidence_type="patch_verification",
            tool="git_diff",
            status="blocked",
            summary=scope_reason,
            confidence_impact="low",
            metadata={"changed_files": changed_before_commit, "allowed_files": scope_allowed},
        ))
        log_agent_event(
            agent="codefix",
            stage="patch_verification",
            status="blocked",
            event_id=event.id,
            incident_id=incident_id,
            changed_files=changed_before_commit,
            allowed_files=scope_allowed,
            summary=scope_reason,
        )
        return CodeFixResult(
            success=False,
            error=f"Patch verification blocked: {scope_reason}",
            branch_name=branch_name,
            confidence=rca_confidence,
            verification_summary="Blocked because changed files did not match RCA buggy_file boundary.",
            verification_records=verification_records,
        )

    verification_records.append(make_evidence_record(
        evidence_id="fix_ev_003",
        evidence_type="patch_verification",
        tool="git_diff",
        status="passed",
        summary=(
            f"Detected changed files before commit: {', '.join(changed_before_commit)}"
            if changed_before_commit
            else "No changed files detected before commit."
        ),
        confidence_impact="high",
        metadata={"changed_files": changed_before_commit},
    ))
    log_agent_event(
        agent="codefix",
        stage="patch_verification",
        status="passed",
        event_id=event.id,
        incident_id=incident_id,
        changed_files=changed_before_commit,
    )

    try:
        commit_msg = (
            f"fix({rca.buggy_function}): {rca.root_cause[:60]}\n\n"
            f"Fixes #{event.external_number or event.external_id or 'unknown'}\n\n"
            f"Root cause: {rca.root_cause}\n"
            f"Confidence: {rca.confidence}"
        )
        changed_files = _commit_and_push(branch_name, commit_msg, repo_dir, event.repo_url)
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_004",
            evidence_type="git",
            tool="git_commit_push",
            status="passed",
            summary=f"Committed and pushed {len(changed_files)} changed file(s).",
            confidence_impact="neutral",
            metadata={"changed_files": changed_files},
        ))
        log_agent_event(
            agent="codefix",
            stage="commit_push",
            status="completed",
            event_id=event.id,
            incident_id=incident_id,
            branch=branch_name,
            changed_files=changed_files,
        )
    except Exception as e:
        log_agent_event(
            agent="codefix",
            stage="commit_push",
            status="failed",
            event_id=event.id,
            incident_id=incident_id,
            summary=str(e),
        )
        return CodeFixResult(
            success=False,
            error=f"Commit/push failed: {e}",
            branch_name=branch_name,
            confidence=rca_confidence,
            verification_summary="Patch could not be committed or pushed.",
            verification_records=verification_records,
        )

    # Step 4 - Open PR
    try:
        pr_url, pr_number = _open_pr(
            repo_full_name=event.repo_full_name,
            branch_name=branch_name,
            base_branch=event.branch,
            event=event,
            rca=rca,
            changed_files=changed_files,
        )
        print(f"[code_fix] PR opened: {pr_url}")
        verification_records.append(make_evidence_record(
            evidence_id="fix_ev_005",
            evidence_type="github",
            tool="github_open_pull_request",
            status="passed",
            summary=f"Opened pull request #{pr_number}.",
            confidence_impact="neutral",
            metadata={"pr_url": pr_url, "pr_number": pr_number},
        ))
        log_agent_event(
            agent="codefix",
            stage="pr_created",
            status="completed",
            event_id=event.id,
            incident_id=incident_id,
            pr_url=pr_url,
            pr_number=pr_number,
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return CodeFixResult(
            success=True,
            pr_url=pr_url,
            pr_number=pr_number,
            branch_name=branch_name,
            files_changed=changed_files,
            patch_summary=patch_summary,
            confidence=rca_confidence,
            verification_summary="Patch was generated, changed files were detected, branch was pushed, and PR was opened.",
            verification_records=verification_records,
        )
    except GithubException as e:
        log_agent_event(
            agent="codefix",
            stage="pr_created",
            status="failed",
            event_id=event.id,
            incident_id=incident_id,
            summary=str(e),
            duration_ms=int((time.perf_counter() - started_at) * 1000),
        )
        return CodeFixResult(
            success=False,
            error=f"PR creation failed: {e}",
            branch_name=branch_name,
            files_changed=changed_files,
            patch_summary=patch_summary,
            confidence=rca_confidence,
            verification_summary="Branch was pushed but PR creation failed.",
            verification_records=verification_records,
        )


def _execute_code_fix_plan(plan: RemediationPlan) -> dict:
    context = plan.execution_context
    event = ErrorEvent(**context["event"])
    rca = RCAResult(**context["rca"])
    result = run_code_fix(
        event,
        rca,
        context["knowledge_id"],
        repo_dir=context.get("repo_dir", CLONE_DIR),
        require_approval=False,
    )
    return result.model_dump()


approval_store.register_executor("code_fix", _execute_code_fix_plan)


# -- Dev test ------------------------------------------------------------------

if __name__ == "__main__":
    from issuelayer.intake.schemas import make_fingerprint
    import uuid

    test_event = ErrorEvent(
        id=str(uuid.uuid4()),
        fingerprint=make_fingerprint(
            "AttributeError",
            "'NoneType' object has no attribute 'query'"
        ),
        error_type="AttributeError",
        message="'NoneType' object has no attribute 'query'",
        traceback=(
            "Traceback (most recent call last):\n"
            "  File \"rag_agent/pipeline.py\", line 45, in run_pipeline\n"
            "    result = self.retriever.retrieve(query)\n"
            "  File \"rag_agent/retriever.py\", line 23, in retrieve\n"
            "    return self.index.query(query)\n"
            "AttributeError: 'NoneType' object has no attribute 'query'"
        ),
        file_path="rag_agent/retriever.py",
        function_name="retrieve",
        line_number=23,
        repo_url="https://github.com/AbbasAziz-dev/rag_agent.git",
        repo_full_name="AbbasAziz-dev/rag_agent",
        external_number=1,
    )

    test_rca = RCAResult(
        root_cause="self.index is None because it was never initialised before retrieve() was called",
        buggy_file="rag_agent/retriever.py",
        buggy_function="retrieve",
        buggy_lines=[23],
        affected_files=["rag_agent/pipeline.py"],
        fix_suggestion="Add a None check for self.index at the start of retrieve() and raise a clear error",
        confidence="high",
        reasoning="Traceback shows self.index.query() raises AttributeError - self.index must be None",
    )

    print("Running Code Fix Agent...")
    result = run_code_fix(test_event, test_rca, knowledge_id="1cacbc66")

    print(f"\nResult:")
    print(f"  Success       : {result.success}")
    print(f"  PR URL        : {result.pr_url}")
    print(f"  Branch        : {result.branch_name}")
    print(f"  Files changed : {result.files_changed}")
    print(f"  Patch summary : {result.patch_summary}")
    if result.error:
        print(f"  Error         : {result.error}")


