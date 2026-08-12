# AMS Monitoring Intake & Categorization Service

Single deployable FastAPI service for the first AMS diagnosis stage, with a lightweight dashboard for inspecting workflow execution state.

Overview

This repository implements a single FastAPI service that accepts incoming incident webhooks (Jira, ServiceNow, GitHub), normalizes them into a shared ErrorEvent shape, and runs an embedded categorization agent to produce an initial classification and optional automated actions (L2 RCA agent / fix agent). The categorization agent is embedded as the Python package `categorization_layer` and is executed locally — there is no separate categorization API called at runtime.

Flow

```text
incident webhook -> connector -> normalizer -> categorization -> optional L2 RCA agent -> optional fix agent -> ServiceNow notification
```

Key components / runtime ownership

- `server.py` — FastAPI entry points, webhook receivers, and dashboard APIs.
- `issuelayer/` — source-specific event creation and normalization (maps incoming payloads to ErrorEvent).
- `workflows/intake_categorisation_workflow.py` — LangGraph orchestration for the intake + categorization workflow.
- `categorization_layer/` — deterministic and agentic categorization logic (embedded agent).
- `categorisation_adapter.py` — maps `ErrorEvent` into the embedded categorization agent and normalizes the agent output shape.

Endpoints

- `POST /webhook/incidents/{source}`
  - `source` must be one of: `jira`, `servicenow`, `github`.
  - This is the generic intake endpoint that routes to the appropriate connector and normalizer.
- `POST /webhook/jira` — Jira-specific webhook endpoint (alias to the generic intake endpoint).
- `POST /webhook/servicenow` — ServiceNow-specific webhook endpoint (alias to the generic intake endpoint).
- `POST /webhook/github` — GitHub-specific webhook endpoint (alias to the generic intake endpoint).
- `GET /health` — healthcheck for liveness/readiness.
- `GET /dashboard/workflow` — returns workflow graph metadata used by the UI to render the workflow.
- `GET /dashboard/executions/latest` — returns the latest recorded execution summary for inspection.

Configuration / required environment variables

The service requires a few environment variables to operate. The agent model and API key are required to run the embedded categorization/agentic logic.

```env
GROQ_API_KEY=...
GROQ_MODEL=llama-3.3-70b-versatile
AI_TIMEOUT=30                  # seconds to allow for AI/agent calls
AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW=true

JIRA_WEBHOOK_TOKEN=...
SERVICENOW_WEBHOOK_TOKEN=...
GITHUB_WEBHOOK_SECRET=...
```

Repo-level defaults (optional)

These repo defaults are only needed when incoming incidents (for example, L3 incidents from Jira/ServiceNow) do not include repository information.

```env
JIRA_DEFAULT_REPO_URL=...
JIRA_DEFAULT_REPO_FULL_NAME=...
JIRA_DEFAULT_BRANCH=main
SERVICENOW_DEFAULT_REPO_URL=...
SERVICENOW_DEFAULT_REPO_FULL_NAME=...
SERVICENOW_DEFAULT_BRANCH=main
```

Run locally

Start the FastAPI server with uvicorn for local development:

```bash
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Notes and development hints

- The embedded categorization agent runs locally via the `categorization_layer` package — make sure `GROQ_API_KEY` and `GROQ_MODEL` are set when you exercise agentic flows.
- `AI_TIMEOUT` controls how long the service will wait for model/agent responses; tune it for your environment.
- If you add or change workflow orchestration, update `workflows/intake_categorisation_workflow.py` and the dashboard endpoints so the UI reflects the current graph.

If you'd like, I can also:
- Add a brief "development setup" section with exact dependency installation steps (pip/poetry/virtualenv) based on this repo's packaging, or
- Add example webhook payloads and curl commands for testing each connector.

Deploy backend on AWS Lambda

This service can run on AWS Lambda behind API Gateway by using the provided
`lambda_handler.py` and `Dockerfile`. A container image is recommended because
the dependency set includes native/heavier packages such as tree-sitter,
psycopg2, Neo4j, Pinecone, LangChain, and LangGraph.

Recommended Lambda environment variables:

```env
GROQ_API_KEY=...
GROQ_MODEL=llama-3.3-70b-versatile
AI_TIMEOUT=30
AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW=true

JIRA_WEBHOOK_TOKEN=...
SERVICENOW_WEBHOOK_TOKEN=...
GITHUB_WEBHOOK_SECRET=...

CLONE_ROOT=/tmp/clone
CACHE_DIR=/tmp/cache
QUEUE_FILE=/tmp/data/event_queue.json
RCA_REPORT_DIR=/tmp/data/rca_reports
```

The `/tmp` paths are important on Lambda because the function code directory is
read-only at runtime. Repo clones, analysis caches, queue files, and RCA reports
must be written to Lambda ephemeral storage or to a durable external store.

Build and push the backend image:

```bash
docker buildx build --platform linux/amd64 --provenance=false -t ams-monitoring-lambda .
aws ecr create-repository --repository-name ams-monitoring-lambda
aws ecr get-login-password --region us-east-1 | docker login --username AWS --password-stdin <account-id>.dkr.ecr.us-east-1.amazonaws.com
docker tag ams-monitoring-lambda:latest <account-id>.dkr.ecr.us-east-1.amazonaws.com/ams-monitoring-lambda:latest
docker push <account-id>.dkr.ecr.us-east-1.amazonaws.com/ams-monitoring-lambda:latest
```

Create or update a Lambda function from that ECR image, then expose it through
API Gateway HTTP API with proxy routes:

```text
ANY /
ANY /{proxy+}
```

Suggested Lambda sizing for the full RCA/code-fix workflow:

```text
Timeout: 300-900 seconds
Memory: 2048 MB or higher
Ephemeral storage: 4096 MB or higher when L3 repo cloning/ingestion is enabled
```

After API Gateway is available, smoke test it:

```bash
python test_ams_monitoring_aws.py --base-url https://your-api-gateway-url
```

Production note: `dashboard_state.py` and `governance/approvals.py` currently
store state in memory. For production Lambda deployments, move dashboard
execution state and approval plans to DynamoDB or Postgres so state survives
cold starts and is shared across concurrent Lambda instances.
