---
name: l3-rca
description: Language-agnostic, evidence-first L3 root cause analysis for code-level incidents using stacktrace data, parsed code facts, Neo4j relationships, and bounded source reads.
---

# L3 RCA Skill

Use this skill for deep code RCA across any programming language. The goal is to identify the causal defect with evidence, not to restate the error message or stacktrace.x`x`x`

## Core Principle
 
RCA must explain this chain:

`trigger -> bad input/state/config/dependency -> failing code path -> observed error`
x`
The failing line is the failure point. It is not automatically the root cause.

## Investigation Order

1. Start from the first application-owned stack frame or the event's file/line/function.
2. Read the failing source range before making claims.
3. Map the failure line to the enclosing function, method, class, module, component, or handler using prefetched context and file summaries.
4. Use `get_function_calls` when the failing function name is available.
5. Use `get_connected_files` and `get_file_summary` to shortlist related files.
6. Read connected files only when they can explain one of:
   - input creation or validation
   - state mutation
   - configuration or dependency setup
   - serialization/deserialization or mapping
   - persistence, cache, network, filesystem, or external API calls
   - caller/callee contract mismatch
7. Stop expanding once the causal path is clear enough to produce evidence-backed RCA.
8. State unknowns explicitly when graph data, source context, or stacktrace data is incomplete.

## Representation and Conversion Checks

- Preserve the exact representation of values from the incident and source. A value
  shown without a prefix in an exception may have been normalized by a parser; inspect
  the original input and the branch that handled it before calling it decimal,
  hexadecimal, binary, octal, encoded, signed, or unsigned.
- For conversion and range failures, prove the conversion rule from source before
  claiming overflow. Check the selected branch, prefix handling, digit count, sign, and
  fallback type before recommending a numeric-type change.

## Universal Failure Heuristics

- Null/None/undefined/nil: identify the exact expression, then trace where that value should have been created, checked, injected, loaded, or returned.
- Type/attribute/member errors: verify the runtime value shape against the function contract and caller behavior.
- Index/key errors: trace collection creation, filtering, bounds assumptions, map/dictionary keys, and empty-result handling.
- Parsing/serialization errors: inspect input format assumptions, schema validation, encoding, and mapper/converter code.
- Database/persistence errors: inspect entity/model state, query assumptions, transaction boundaries, migrations/schema, and repository/DAO calls.
- Network/API errors: inspect request construction, auth/config, response handling, retries, and error mapping.
- Concurrency/async errors: inspect lifecycle, initialization order, awaited promises/tasks/futures, shared mutable state, and race-sensitive code.
- Configuration errors: inspect env vars, config files, defaults, dependency injection, and startup wiring.

## Evidence Rules

- Every root-cause claim must be supported by at least one source line, stacktrace frame, graph relationship, or prefetched context item.
- Prefer direct evidence from source reads over assumptions from names.
- Do not mark confidence as `high` unless the failing code was read and the causal path is clear.
- Do not mark confidence as `high` when the file and line are correct but the causal
  explanation depends on an unverified interpretation of the input representation.
- Use `medium` when the likely cause is supported but one important caller/config/input source was not available.
- Use `low` when RCA is mostly based on the stacktrace or incomplete context.
- Do not list unrelated connected files as affected files.

## Fix Direction Rules

- Recommend the fix at the boundary where the broken contract should be enforced.
- If invalid external input caused the failure, prefer validation near input boundaries.
- If internal state violated an invariant, prefer fixing initialization/state transitions.
- If an external dependency can return missing/error data, prefer explicit error handling at the call site.
- Avoid recommending silent defaults that hide corrupt or invalid data unless the domain clearly supports that behavior.

## Output Discipline

Return only the expected RCA JSON shape. Keep `reasoning` concise and causal:

- observed failure
- source evidence
- relationship evidence
- root cause
- fix direction
