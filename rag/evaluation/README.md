# RAG Evaluation Dataset

This folder contains gold test cases and an offline ablation runner for evaluating retrieval quality before RCA runs.

## Files

- `test_events.jsonl`: machine-readable gold dataset.
- `evaluate_rag.py`: runs retrieval variants and writes result tables.
- `results/`: generated output directory, ignored by normal review unless you intentionally want to share a run.

## What Each Case Tests

Each row asks: given the incident text, can retrieval find the expected artifact?

- `expected_file`: repo-relative file that should be retrieved.
- `expected_function`: function or method associated with the incident.
- `expected_lines`: line or small line range that should overlap the retrieved evidence.
- `expected_root_cause`: short human-written explanation used for review.
- `case_type`: `strong_traceback` has direct stack-trace evidence; `weak_description` removes exact file/line details.

## Variants

The evaluator runs the same cases with different RAG components enabled:

- `full_rag`: BM25 + semantic + RRF + reranker + verification + same-file context.
- `bm25_only`: lexical keyword search only.
- `semantic_only`: vector search only.
- `bm25_semantic_rrf`: lexical + semantic + RRF, without reranker/context expansion.
- `no_reranker`: full retrieval without reranking.
- `no_verification`: full retrieval without file-existence verification.
- `no_context_expansion`: full retrieval without neighboring chunk expansion.
- `traceback_only`: stack-trace file/function/line hint only.

## Run

First make sure the repo has already been ingested so the lexical index exists under `CACHE_DIR/<knowledge_id>/rag/lexical_index.json`. Semantic variants also need the configured vector provider to contain vectors for the same `knowledge_id`.

Example:

```powershell
python rag/evaluation/evaluate_rag.py `
  --repo-dir "C:\Users\SharvitNileshKashika\Downloads\MyRag" `
  --knowledge-id "<knowledge_id_from_ingestion_logs>"
```

To run only local/no-vector variants:

```powershell
python rag/evaluation/evaluate_rag.py `
  --repo-dir "C:\Users\SharvitNileshKashika\Downloads\MyRag" `
  --knowledge-id "<knowledge_id_from_ingestion_logs>" `
  --variants bm25_only traceback_only
```

## Outputs

The script writes:

- `rag/evaluation/results/rag_ablation_results.csv`
- `rag/evaluation/results/rag_ablation_summary.csv`
- `rag/evaluation/results/rag_ablation_results.json`
- `rag/evaluation/results/rag_ablation_summary.md`

## Metrics

Metrics are reported separately so the report stays explainable:

- `file_hit_at_1`, `file_hit_at_3`, `file_hit_at_5`
- `top_rank`, `mrr`
- `function_hit`
- `line_overlap`, `exact_line_hit`
- `evidence_has_error_terms`
- `evidence_support`, for example `3/4` checks passed
- `retrieval_confidence`
- `false_high_confidence`
- `latency_ms`
- `failure_reason`
## Generate Defects4J Cases

Use `generate_defects4j_dataset.py` from WSL/Linux after Defects4J is installed and `defects4j` is available on `PATH`.

Example for 10 real Java bugs:

```bash
cd /mnt/c/Users/SharvitNileshKashika/Downloads/ams_monitoring-
python3 rag/evaluation/generate_defects4j_dataset.py \
  --defects4j-root /mnt/c/Users/SharvitNileshKashika/defects4j \
  --work-root /mnt/c/Users/SharvitNileshKashika/d4j-work \
  --bugs "Lang:1,3,5,7,10;Math:1,5,10;Codec:1;Csv:1" \
  --output rag/evaluation/defects4j_test_events.jsonl
```

The script checks out each buggy Defects4J version, runs the failing test, reads the official patch, and writes JSONL rows with:

- `incident_text`: failing test/error output used as the RAG query.
- `expected_file`: source file changed by the official Defects4J patch.
- `expected_function`: best inferred Java method around the patch line.
- `expected_lines`: patch line area used for line-overlap scoring.
- `repo_dir`: local checked-out buggy repo path for that row.

The current evaluator accepts one `repo-dir` and one `knowledge_id` per run. For Defects4J, evaluate each generated row against its own checked-out repo, or extend the evaluator later to support per-row `repo_dir` and `knowledge_id`.
