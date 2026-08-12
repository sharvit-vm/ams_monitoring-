---
name: codefix
description: Language-agnostic, minimal, evidence-backed code fix workflow for applying RCA findings safely and producing a small patch.
---

# Code Fix Skill

Use this skill when applying an automated code fix from an RCA result. The goal is a small, correct, reviewable patch that addresses the root cause.

## Patch Policy

- Fix the root cause identified by RCA, not only the immediate exception symptom.
- Make the smallest coherent change that resolves the bug.
- Preserve existing public behavior unless the RCA shows that behavior is wrong.
- Preserve local style, naming, indentation, error handling, and import organization.
- Prefer existing project patterns over new abstractions.
- Do not refactor unrelated code.
- Treat `buggy_file` as the only automatic edit target unless the orchestrator explicitly provides additional allowed edit files.
- Use `affected_files` and connected files as evidence/context only; do not edit them automatically.
- If the apparent fix belongs in a caller/controller/related file that is not an allowed edit file, stop and report the blocker instead of patching it.
- Do not introduce broad dependencies, schema changes, API changes, or config changes unless the RCA requires them.

## Required Workflow

1. Read the buggy file before editing.
2. Use RCA evidence to identify the exact replacement range.
3. If the fix location is ambiguous, inspect function calls, file summaries, or affected files before editing.
4. Patch only the minimal affected range with `write_fix`; replace the unsafe line/block rather than inserting a second version beside it.
5. Re-read the changed range with `read_file_range`.
6. Ensure the changed code still fits surrounding control flow, scope, declarations, and indentation.
7. Mentally execute the original failing path against the patched code. The failing input must reach the new guard/fix before it can reach the old crash point.
8. Output the required JSON summary only.

## Universal Fix Heuristics

- Null/None/undefined/nil: add validation or initialization before the first use/dereference; do not silently continue with invalid data unless the domain expects it.
- When adding a guard, remove or move any earlier statement that still dereferences/uses the invalid value before the guard.
- Type/attribute/member mismatch: fix the producer/caller contract when possible; add local guards only when multiple callers can legitimately pass mixed shapes.
- Index/key missing: handle empty/missing cases explicitly and preserve meaningful error messages.
- Parsing/serialization: validate input shape before parsing or map failures to clear domain errors.
- Database/persistence: preserve transaction and model conventions; avoid partial writes.
- Network/API: handle failed/missing responses explicitly; avoid swallowing errors.
- Config/startup: prefer clear startup/config validation over late runtime failure.
- Async/concurrency: preserve await/task/future semantics; avoid blocking calls inside async paths unless the project already does so.

## Safety Checks Before `write_fix`

- The target file was read successfully.
- The replacement range is inside the intended function/method/block.
- The patch does not remove unrelated behavior.
- New names are already imported/defined or the replacement includes the required local import/change.
- The replacement will not create duplicate declarations, duplicate assignments, unreachable code, or two competing versions of the same logic in one scope.
- If the fix adds validation, the validation runs before any operation that would crash on the invalid value.
- The patch summary can explain exactly why the change fixes the RCA.

## Safety Checks After `write_fix`

- Re-read the changed block, not just the exact changed lines if surrounding context is needed.
- Confirm the original unsafe statement is gone or moved after the guard.
- Confirm each introduced local variable is declared once in its scope.
- Confirm the original failing input now follows a safe path.
- If the reread shows the bug still occurs before the guard, or the patch introduces an obvious syntax/scope issue, call `write_fix` again with a corrected minimal replacement before producing the final JSON.

## Stop Conditions

Do not apply a speculative patch if:

- RCA confidence is low and the buggy location is unclear.
- The required file cannot be read.
- The proposed fix would require multiple unrelated refactors.
- The code path cannot be located from RCA, graph tools, or source reads.
- The fix requires product/domain knowledge that is absent from the event, code, and graph context.

If the surrounding prompt requires a patch but these stop conditions apply, make the safest minimal diagnostic change only when it is clearly useful; otherwise report the blocker in the JSON summary.
