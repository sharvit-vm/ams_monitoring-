# AMS Monitoring Incident Resolution Platform

An incident intake, categorisation, RCA, and governed remediation service for
Jira, ServiceNow, and GitHub incidents.

## 1. Business Use Case

Application support teams receive incidents from multiple systems. Each source
has a different payload shape and a different amount of technical information.
One incident may contain an exact Java traceback, while another contains only a
short description such as "client lookup fails".

Without a common workflow, engineers must manually validate the event, remove
source-specific differences, decide whether it is L1/L2/L3, identify the
affected code or operational system, gather evidence, propose a safe fix,
obtain approval, and report the result.

This service turns that work into a repeatable, auditable process:

    Accept -> Normalize -> Protect -> Categorize -> Investigate
           -> Produce RCA -> Approve -> Remediate -> Report

The model is a reasoning component, not the system of record. The workflow
uses the original incident, parsed source structure, RAG results, Neo4j
relationships, bounded source reads, validation, approvals, and reports to
control and audit the model.

## 2. High-Level Architecture

    Jira / ServiceNow / GitHub
              |
              v
    Connector and authentication
              |
              v
    SourceEvent
              |
              v
    Normalizer -> common ErrorEvent
              |
              v
    SQLite job store and workflow worker
              |
              v
    Guardrails and categorisation
              |
        +-----+-----+----------------+
        |           |                |
        v           v                v
       L1          L2               L3
    placeholder  operational     code RCA
                 RCA             and code fix
                      \           /
                       v         v
                  approval and response

The main orchestration is implemented in
workflows/intake_categorisation_workflow.py.

The important runtime boundary is the SQLite job store. The webhook validates
and enqueues an incident, returns HTTP 202, and a background worker performs
the long-running workflow. The request is therefore not held open while
repository ingestion, L2 RCA, L3 RCA, or code fix runs.

### 2.1 Queue, SQLite, and worker execution

The service uses SQLite through storage/job_store.py. The default database is:

    data/workflow.sqlite3

The database contains:

- jobs: queued incident and remediation jobs;
- snapshots: dashboard execution state for each workflow session;
- approvals: durable approval plans.

The job store provides:

- atomic enqueue and claim transactions;
- active-job deduplication;
- pending/running/completed/failed/interrupted states;
- ordering by creation time;
- restart recovery;
- durable dashboard snapshots;
- durable approval records.

The worker is implemented in workflows/job_worker.py. It:

1. claims one pending job;
2. runs the incident or approved-remediation workflow;
3. writes execution state and final status;
4. marks the job completed or failed;
5. records the exception type without exposing sensitive exception text.

Only one worker process is allowed for the local SQLite/shared-checkout
configuration. A lock file prevents multiple Uvicorn processes from
simultaneously changing the same clone and database. This is suitable for a
single-host deployment. Multiple application instances require a shared queue
and shared state service such as SQS, Redis Streams, DynamoDB, or Postgres.

If the process stops while a job is running, startup marks that job
interrupted. The service does not automatically replay unknown side effects
such as an already-created pull request or database change. An operator must
review and resubmit when appropriate.

## 3. Repository Structure

| Area | Location | Responsibility |
|---|---|---|
| HTTP service | server.py | FastAPI webhooks and dashboard APIs |
| Intake | issuelayer/ | Source events, connectors, normalization |
| Workflow | workflows/ | LangGraph orchestration and routing |
| Categorisation | categorization_layer/ and categorisation_adapter.py | Initial classification |
| L3 RCA | agents/l3_rca/ | Code evidence and structured RCA |
| Code fix | agents/code_fix.py | Controlled patch generation and approval |
| L2 RCA | l2_rca/ | Operational RCA agent |
| Fix agents | db_fix/ and workflows/agent_registry.py | Domain remediation |
| Repository ingestion | phases/ | Scan, parse, summarize, hierarchy, graph |
| Hybrid RAG | rag/ | Chunking, BM25, semantic search, RRF |
| Graph access | tools/ and phases/neo4j_ingest.py | Neo4j evidence and relationships |
| Governance | governance/ | Approval plans and continuation |
| Observability | observability/ | Logs, token usage, traces, Langfuse |
| Reports | storage/ and data/ | RCA report persistence |
| Frontend | frontend/ | Workflow dashboard |

## 4. Intake and Routing

Supported endpoints:

    POST /webhook/jira
    POST /webhook/servicenow
    POST /webhook/github
    POST /webhook/incidents/{source}

Authentication is checked before queueing:

- Jira uses JIRA_WEBHOOK_TOKEN when configured.
- ServiceNow uses SERVICENOW_WEBHOOK_TOKEN when configured.
- GitHub validates its signature with GITHUB_WEBHOOK_SECRET.

The service returns an asynchronous job response:

~~~json
{
  "status": "accepted",
  "job_id": "job-id",
  "workflow_session_id": "job-id",
  "duplicate": false,
  "status_url": "/dashboard/jobs/job-id"
}
~~~

Each connector creates a SourceEvent. The normalizer converts it into the
shared ErrorEvent contract containing incident identifiers, description,
error type, message, traceback, file/function/line hints, repository fields,
priority, labels, comments, and operational context.

L3 uses repository context: repo_url, repo_full_name, and branch. L2 uses
operational context: application, service, team, or configuration item.
Keeping these contexts separate prevents an operational service name from
being mistaken for a confirmed code location.

Guardrails inspect the source, headers, payload, and normalized event. A
blocked event stops for manual intervention. Credentials and authentication
headers are not copied into workflow state.

Categorisation decides validity, duplicate status, category, technology, risk,
criticality, support level, human-review requirement, and next action:

    L1            -> L1 placeholder
    L2            -> operational L2 RCA
    L3            -> code-level L3 RCA
    human_review  -> stop and wait
    reject        -> terminal rejected state

Categorisation is a routing decision, not the final root-cause answer.

## 5. L3 Code RCA Flow

L3 is used when the incident requires repository-level investigation.

    Categorisation = L3
            |
            v
    Repository checkout
            |
            v
    Resolve incident file path
            |
            v
    Repository ingestion
            |
            +--> scanner
            +--> parser and file analysis
            +--> LLM summaries
            +--> hierarchy
            +--> Neo4j graph
            +--> lexical RAG
            +--> optional semantic RAG
            |
            v
    Evidence collection
            |
            +--> traceback and file/line evidence
            +--> RAG candidate retrieval
            +--> Neo4j graph expansion
            +--> bounded source reads
            |
            v
    Structured RCA and validation
            |
            v
    Save RCA report -> code-fix agent -> human approval

### 5.1 Checkout and knowledge ID

The workflow clones or updates the repository under CLONE_ROOT. For GitHub
repositories, GITHUB_TOKEN is used during clone/fetch and the clean repository
URL is restored afterward.

A stable knowledge_id scopes the repository's Neo4j nodes, RAG chunks, and
evidence queries. This prevents similarly named files from different
repositories being mixed together. If repository information is missing, L3
stops instead of guessing.

### 5.2 Path resolution

Providers may send package-qualified paths, Windows separators, or paths that
include repository prefixes. The workflow resolves the event path against the
checkout using normalized separators, suffix matching, and Java
class/package information. The resolved repository-relative path is used by
source reads and graph queries.

### 5.3 Repository ingestion

The ingestion pipeline is:

    scanner
      -> file analysis and language parser
      -> LLM file and folder summaries
      -> hierarchy builder
      -> Neo4j ingestion
      -> RAG index creation

Parsers extract functions, classes, imports, symbols, line ranges, and
relationships. Implementations under parsers/ currently cover Python,
JavaScript, TypeScript, Go, and Java.

LLM summaries provide bounded context. They are not treated as stronger
evidence than the actual source file.

### 5.4 Ingestion performance and parallelism

The ingestion stages have different dependency rules:

- scanning and parsing must establish the file and symbol inventory first;
- file summaries can be generated independently after parsing;
- hierarchy summaries are parallel within each hierarchy level, but parent
  summaries wait for child summaries;
- Neo4j relationships depend on the parsed nodes and are written in batches;
- RAG indexing builds the lexical index locally and uploads semantic vectors
  in batches.

LLM file summaries use a bounded ThreadPoolExecutor. The relevant settings are:

~~~env
LLM_ANALYSIS_MAX_WORKERS=4
LLM_ANALYSIS_BATCH_SIZE=4
~~~

The executor is reused for the complete run. Batches act as checkpoints and
limit the number of in-flight provider calls. This improves throughput while
protecting the model provider from an unbounded request burst.

Hierarchy summaries also use bounded workers:

~~~env
HIERARCHY_MAX_WORKERS=4
MAX_HIERARCHY_LEVELS=8
~~~

Hybrid retrieval runs BM25 and semantic retrieval concurrently with two
workers. The results are then fused, reranked, verified, and expanded. This
parallelism is safe because the two retrieval operations are independent.

Neo4j ingestion uses UNWIND-style batch writes rather than one database
transaction per node. Java call resolution also prepares relationship batches
before writing them.

The stages are not fully parallel end to end. The overall order remains
deliberately sequential where later stages need earlier output:

    scan -> parse -> summaries -> hierarchy -> graph/RAG

This is a correctness boundary, not an accidental performance limitation.
Parallelizing dependent hierarchy levels or graph writes before their nodes
exist could create incomplete or incorrectly linked evidence.

### 5.5 RAG chunking

RAG chunks preserve file path, start/end line, language, content type, symbol,
imports, called symbols, stable chunk ID, content hash, and same-file
neighbors. Parser-derived functions and classes are preferred boundaries for
code. Large content is split by the configured maximum size. Markdown and
other documentation are split into sections while retaining file/line
provenance.

Defaults:

~~~env
RAG_CHUNK_MAX_CHARS=6000
MAX_CHUNK_TOKENS=800
~~~

MAX_CHUNK_TOKENS applies to embedding/vector ingestion. Retrieval results
should remain traceable to a real file and line range.

### 5.6 BM25 lexical retrieval

The lexical index uses a BM25-style implementation. It is strong for exact
exception names, method/class names, file names, distinctive log messages,
and exact ticket wording. BM25 uses term frequency, inverse document
frequency, document length, and exact symbol/file matching.

### 5.7 Semantic retrieval

When enabled, chunks are embedded and stored in the configured vector
provider:

~~~env
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
~~~

Semantic retrieval helps when incident wording and source wording differ. It
is optional:

~~~env
VECTOR_STORE_PROVIDER=none
AUTO_VECTOR_INGEST_ON_WEBHOOK=false
~~~

Lexical retrieval, traceback evidence, bounded source reads, and Neo4j can
still support L3 when semantic vectors are disabled.

### 5.8 Reciprocal Rank Fusion

When BM25 and semantic retrieval are both enabled, ranked lists are combined:

    RRF score = sum of 1 / (k + rank)

The default RRF constant is 60. RRF creates a stronger candidate list; it
does not by itself prove that a candidate is the defect.

~~~env
RAG_BM25_TOP_K=50
RAG_SEMANTIC_TOP_K=50
RAG_RRF_TOP_K=30
RAG_RERANK_TOP_K=12
RAG_RETRIEVAL_TOP_K=8
RAG_RRF_K=60
RAG_CHUNK_MAX_CHARS=6000
RAG_VECTOR_UPSERT_BATCH=50
~~~

### 5.9 Neo4j graph expansion

Neo4j stores files, functions, classes, folders, hierarchy levels, imports,
calls, callers, callees, ownership, and summaries.

The intended relationship is:

    traceback or RAG finds the primary candidate
            |
            v
    Neo4j expands callers, callees, imports, and module context
            |
            v
    source tools verify the exact behavior

Neo4j enriches and verifies the primary candidate. It should not replace the
traceback or make an unrelated connected file the primary bug location.

### 5.10 Evidence-backed RCA

The L3 agent gathers bounded context from the traceback, RAG, Neo4j, summaries,
folders, functions, callers, callees, and source ranges. It does not need the
entire repository in one prompt.

The structured result contains:

    root_cause, buggy_file, buggy_function, buggy_lines
    affected_files, fix_suggestion, reasoning
    evidence, evidence_records, analysis_facts
    confidence, confidence_score, confidence_breakdown, token_usage

Reports are saved under data/rca_reports/<event-id>.json.

Confidence is an evidence-coverage heuristic, not a probability:

    0.80 and above -> high
    0.50 to 0.79  -> medium
    below 0.50    -> low

Unresolved causal or semantic validation can cap the score for safety. A
score of 0.49 means the system refused to treat the causal explanation as
verified. Confidence and remediation readiness are separate gates.

### 5.11 Code-fix and approval

After the report is saved, the code-fix agent loads its skill, reads the RCA
evidence and target source, proposes the smallest evidence-backed patch, uses
controlled write tools, validates the changed range, creates an approval plan,
waits for approval when required, and then commits, pushes, and creates a pull
request when configured.

The approval record contains the plan, approver, reason, status, and workflow
session ID. The model proposes a change; it does not independently authorize
the change.

## 6. L2 Operational RCA Flow

L2 is used for database, configuration, cache, dependency, service, and
environment problems.

    Categorisation = L2
            |
            v
    Build operational L2 request
            |
            v
    L2 RCA agent
            |
            v
    Structured operational RCA
            |
            +--> registered domain fix agent, when applicable
            +--> human approval, when required
            |
            v
    Final response and notification

The L2 request contains ticket ID, classification, support level, technology,
team, application/configuration item, title, description, priority, and
categorisation confidence.

L2 does not require a repository, Neo4j, or code checkout. The workflow reads
the L2 problem domain and uses workflows/agent_registry.py to select a
registered fix agent. The database fix implementation is under db_fix/.

If RCA succeeds but no fix agent is registered for the domain, the fix-agent
step is recorded as skipped with a reason. That is different from an L2 RCA
failure. Risky L2 remediation follows the same approval and audit rules as
code remediation.

## 7. L1, Human Review, and Notifications

L1 is currently a placeholder route. It records that L1 RCA/remediation is
not implemented and returns a terminal workflow state.

Human review is used when guardrails block the event, categorisation is
uncertain, the event is invalid/incomplete, or policy requires review.

The dashboard exposes workflow state and pending approval plans. Notification
steps use the source platform selected for the incident.

## 8. Sessions, Dashboard, and Langfuse

Every accepted incident receives a workflow_session_id. Workflow stages and
LLM observations should use the same session ID so one incident appears as one
trace tree.

The dashboard state is written to the SQLite snapshots table and published to
connected frontend clients through a server-sent events stream. The frontend
can therefore show progress without repeatedly submitting the incident or
keeping the original webhook request open.

Token usage is collected with a request-local ContextVar and LangChain callback
collector. It records model calls, input tokens, output tokens, total tokens,
reported calls, and model names when the provider returns usage metadata.
Stage totals are kept separately for categorisation, L2, L3 context ingestion,
L3 RCA, and code fix. A session total is an aggregation across those stages,
not the token count of one model generation.

~~~text
GET /dashboard/workflow
GET /dashboard/executions/latest
GET /dashboard/executions/stream
GET /dashboard/approvals/pending
GET /dashboard/jobs/{job_id}
~~~

Langfuse is optional observability. When enabled, generations can include the
model name, input/output tokens, total tokens, cost when pricing is
configured, session relationships, and workflow-stage metadata.

An RCA result's token usage, one model generation's usage, one workflow
stage's usage, and the complete session total are different aggregation
scopes.

## 9. Requirements and Configuration

Runtime requirements:

- Python 3.10 or newer; Python 3.11 recommended.
- Git.
- Virtual environment.
- Network access to the configured model, Neo4j, and repository providers.

SQLite does not require a separate Python package or database server here.
sqlite3 is part of the Python standard library. The application creates
data/workflow.sqlite3 automatically. The database is durable across process
restarts on the same host, but it is not a replacement for a shared
multi-instance production queue.

Install:

~~~powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
~~~

Core variables:

~~~env
GROQ_API_KEY=...
GROQ_MODEL=llama-3.3-70b-versatile
AI_TIMEOUT=30
JIRA_WEBHOOK_TOKEN=...
SERVICENOW_WEBHOOK_TOKEN=...
GITHUB_WEBHOOK_SECRET=...
JIRA_DEFAULT_REPO_URL=...
JIRA_DEFAULT_REPO_FULL_NAME=...
JIRA_DEFAULT_BRANCH=main
SERVICENOW_DEFAULT_REPO_URL=...
SERVICENOW_DEFAULT_REPO_FULL_NAME=...
SERVICENOW_DEFAULT_BRANCH=main
~~~

L3 and repository variables:

~~~env
CLONE_ROOT=clone
CACHE_DIR=cache
RCA_REPORT_DIR=data/rca_reports
AUTO_INGEST_ON_WEBHOOK=true
AUTO_RAG_ON_WEBHOOK=true
AUTO_VECTOR_INGEST_ON_WEBHOOK=false
NEO4J_URI=neo4j+s://...
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=...
GITHUB_TOKEN=...
~~~

L2 and observability variables:

~~~env
L2_DEFAULT_APPLICATION=...
L2_DEFAULT_TECHNOLOGY=...
AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW=true
LANGFUSE_ENABLED=true
LANGFUSE_PUBLIC_KEY=...
LANGFUSE_SECRET_KEY=...
LANGFUSE_HOST=https://cloud.langfuse.com
LANGFUSE_BASE_URL=https://cloud.langfuse.com
LANGFUSE_FLUSH_ON_SPAN=true
~~~

Vector variables:

~~~env
VECTOR_STORE_PROVIDER=none
EMBEDDING_MODEL=text-embedding-3-small
EMBEDDING_DIMENSIONS=1536
~~~

Use a vector provider only when its credentials and index/collection are
configured. The vector dimension must match the embedding model and index.
Never commit .env files, provider keys, webhook tokens, database passwords,
or credential-bearing repository URLs.

## 10. Local Development and Testing

Start the backend:

~~~powershell
.\venv\Scripts\Activate.ps1
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
~~~

Health check:

~~~powershell
curl.exe http://127.0.0.1:8000/health
~~~

Start the frontend in another terminal:

~~~powershell
cd frontend
npm install
npm run dev -- --host 0.0.0.0
~~~

Example Jira request:

~~~powershell
$headers = @{ "Content-Type" = "application/json"; "X-CodeFixer-Token" = $env:JIRA_WEBHOOK_TOKEN }
$body = @{ event = "work_item_created"; issue_key = "LOCAL-001"; summary = "Client lookup fails when email is missing"; description = "java.lang.NullPointerException: email is null"; priority = "High"; labels = "codefix"; repo_url = "https://github.com/example/example.git"; repo_full_name = "example/example"; branch = "main" } | ConvertTo-Json -Depth 10
Invoke-RestMethod -Uri "http://127.0.0.1:8000/webhook/jira" -Method Post -Headers $headers -Body $body
~~~

The response is asynchronous. Inspect the job:

~~~powershell
$jobId = "<job-id>"
Invoke-RestMethod -Uri "http://127.0.0.1:8000/dashboard/jobs/$jobId" | ConvertTo-Json -Depth 30
~~~

Inspect pending approvals:

~~~powershell
Invoke-RestMethod -Uri "http://127.0.0.1:8000/dashboard/approvals/pending" | ConvertTo-Json -Depth 30
~~~

Use the approval_id returned by the service. Never use a placeholder ID.

Run checks:

~~~powershell
pytest
python -m py_compile server.py models.py workflows\intake_categorisation_workflow.py
~~~

RAG evaluation assets and ablation commands are under rag/evaluation/.

## 11. Deployment

Render start command:

~~~text
uvicorn server:app --host 0.0.0.0 --port $PORT
~~~

Set provider keys, webhook tokens, repository defaults, Neo4j credentials,
and Langfuse credentials in Render environment variables. Do not expose
secrets through the frontend build.

EC2 or Windows host:

~~~powershell
.\venv\Scripts\Activate.ps1
uvicorn server:app --host 0.0.0.0 --port 8000
~~~

If 127.0.0.1 works but the public URL fails, check the host firewall, cloud
security group, API Gateway or reverse-proxy target, bind address, and
frontend API base URL.

The SQLite job store, approval state, dashboard snapshots, cache, clones, and
reports are appropriate for controlled local or single-host testing. A
multi-instance deployment needs shared durable services:

    SQS or Redis Streams -> workflow queue
    DynamoDB or Postgres -> incident, approval, execution state
    Object storage -> RCA reports and large evidence
    Neo4j -> shared code graph
    Vector provider -> shared semantic index

## 12. Troubleshooting

Service unavailable:

~~~powershell
curl.exe http://127.0.0.1:8000/health
~~~

If local health succeeds but the public URL fails, check firewall rules,
security groups, API Gateway or reverse-proxy routing, bind address, and the
frontend API base URL.

L3 does not start:

- check repository fields and Git credentials;
- check Neo4j connectivity;
- check writable clone and cache directories;
- check AUTO_INGEST_ON_WEBHOOK.

L2 is empty or its fix agent is skipped:

- check that categorisation selected L2;
- check operational application context;
- check that the L2 result is structured;
- check that the problem domain has a registered fix agent.

Semantic RAG returns no results:

- check AUTO_VECTOR_INGEST_ON_WEBHOOK and VECTOR_STORE_PROVIDER;
- check provider credentials and collection/index;
- check matching embedding dimensions;
- check matching knowledge_id.

Langfuse shows zero cost:

- check LANGFUSE_ENABLED;
- check that every generation has a model name and usage;
- check that provider usage is forwarded;
- check that model pricing is configured in Langfuse.

## 13. Security and Governance

- Authenticate webhooks before queueing or processing.
- Keep provider keys and database credentials on the backend.
- Redact tokens and authorization headers from logs and reports.
- Preserve source event, normalized event, evidence, RCA, plan, and response
  as separate concepts.
- Treat RAG and Neo4j results as candidates that require validation.
- Keep the traceback or retrieval-supported file as the primary L3 entry.
- Use Neo4j to expand and verify context, not replace primary evidence.
- Make the smallest patch supported by the RCA.
- Require approval before risky remediation.
- Record approvals, changes, and external notifications.
- Use shared durable storage before deploying multiple workers or instances.

## 14. Current Scope

Implemented:

- Jira, ServiceNow, and GitHub intake;
- common event normalization;
- guardrails and categorisation;
- L3 repository ingestion;
- Neo4j code graph;
- lexical and optional semantic RAG;
- RRF retrieval fusion;
- structured L3 RCA and confidence;
- embedded L2 RCA;
- registered domain fix-agent routing;
- code-fix approval workflow;
- dashboard execution state;
- optional Langfuse tracing.

