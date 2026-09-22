# L3 RCA evidence and verification

A uniquely resolved application traceback frame with an in-range source line is
the primary investigation location. Otherwise RAG supplies the primary candidate.
Retrieval still enriches traceback-backed investigations. Neo4j expands that entry
and retrieval alternatives, bounded to three unique candidates. Empty or failed
retrieval does not invalidate independently supported source evidence. SourceEvidence reads
bounded ranges from the checkout and records an evidence ID, knowledge ID, file
path, exact excerpt, range, and SHA-256 of the file. It is local to one RCA run.
The knowledge ID identifies the supplied index; the file hash detects changes
after collection. This does not independently prove the index matches a Git commit.

The investigator references source IDs in analysis_facts.evidence_ids. Additional
ranges can be collected with read_source_evidence. The application populates
citations from those records. Unknown IDs and changed source fail validation.
Defect locations contain a line, justification, and evidence IDs; buggy_lines is
derived from those locations. Broad chunks remain supporting evidence. Empty
defect_locations can describe a function-level diagnosis, but cannot authorize a patch.

## Separate decisions

Reported traceback frames remain supplied incident evidence, in their original
order. Static graph callers are possible relationships, not proof of runtime
execution. The report separates execution_path from related_paths. cause_scope
identifies a local defect, upstream trigger, or undetermined cause; unknown upstream
origins alone do not disprove an evidenced local mechanism. Material uncertainties
about the claimed mechanism still prevent a supported causal verdict.

confidence_breakdown preserves incident_evidence and entry_status (traceback-backed,
retrieval-backed, or unresolved). The bundle records location warnings and explicitly
does not claim revision alignment; conflicting source must be reviewed. A RAG rank is
not proof of causality: source references and independent causal review still apply.

- evidence_validity: whether citations are source-backed and available.
- causal_review.verdict: supported, contradicted, or insufficient_evidence for
  the observed cause. Material causal uncertainties belong here.
- causal_review.location_verdict: whether the proposed defect lines are supported.
- remediation_readiness.verdict: ready, needs_investigation, or blocked for patch
  generation. This is not a claim that a patch has passed tests.

These values live in confidence_breakdown, marked verification_version=2. Report
fields used by existing consumers remain present. Root cause, reasoning, and fix
direction remain visible even when unverified; consumers must use the verification
statuses. An unresolved repair does not erase a supported root cause. The numeric
confidence is an evidence-coverage heuristic, not a calibrated probability.
Unsupported causes remain capped below the codefix threshold. Verified source
obtained via RAG counts as source evidence, including when the incident lacks a file.

The reviewer sees source records separately from trimmed retrieval context. It can
request up to three additional repository ranges. One bounded correction/review
cycle follows. Read budgets and repository containment apply to these reads too.
There is no arbitrary retry loop, application execution, or failing-test execution.

review_history preserves drafts, bound diagnoses, and review decisions.
repair_status records completion, unchanged output, or exception type/HTTP status.
source_collection_errors reports failed reads. Canonical source records are saved
in evidence_records. Logs separate cause assessment from remediation readiness.

Codefix requires valid source, a supported cause, and ready remediation for version
2 reports. Human-approval resume preserves the full L3 report and repeats this gate.
Older reports retain the existing low-confidence policy. Existing patch validation
and approval checks still apply.

## Verification

Run offline regression tests with:

```powershell
.\venv\Scripts\python.exe -B -m unittest discover -s tests -p 'test_rca*.py'
```

No re-ingestion is needed for these RCA-only changes. Keep the checkout and index
on the same snapshot. Live model evaluation is separate from offline tests: use
incident input without expected file/function labels or reference patches, then
compare saved reports against those labels. High confidence alone is not proof.
