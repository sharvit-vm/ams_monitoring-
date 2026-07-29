# AMS Monitoring Intake + Categorisation Service

Single deployable FastAPI service for the first AMS diagnosis stage.

Flow:

```text
incident webhook -> connector -> normaliser -> local categorisation LangGraph node -> optional L2 RCA agent -> optional DB Fix agent
```

This service embeds the categorisation agent as the `categorization_layer` Python package. It does not call a separately deployed categorisation API.

## Endpoints

- `POST /webhook/incidents/{source}` where `source` is `jira`, `servicenow`, or `github`
- `POST /webhook/jira`
- `POST /webhook/servicenow`
- `POST /webhook/github`
- `GET /health`

## Runtime Ownership

- `issuelayer/` owns source event creation and normalisation.
- `workflows/intake_categorisation_workflow.py` owns LangGraph orchestration.
- `categorization_layer/` owns deterministic and agentic categorisation logic.
- `categorisation_adapter.py` maps `ErrorEvent` into the embedded categorisation agent and normalises the result shape.

## Required Environment Variables

```env
GROQ_API_KEY=...
GROQ_MODEL=llama-3.3-70b-versatile
AI_TIMEOUT=30
AUTO_ROUTE_L2_WITHOUT_HUMAN_REVIEW=true
L2_RCA_AGENT_URL=http://localhost:8001
L2_RCA_TIMEOUT=120
DB_FIX_AGENT_URL=http://localhost:8002

JIRA_WEBHOOK_TOKEN=...
SERVICENOW_WEBHOOK_TOKEN=...
GITHUB_WEBHOOK_SECRET=...
```

Repo defaults are optional and only needed when Jira/ServiceNow L3 incidents do not include repo fields:

```env
JIRA_DEFAULT_REPO_URL=...
JIRA_DEFAULT_REPO_FULL_NAME=...
JIRA_DEFAULT_BRANCH=main
SERVICENOW_DEFAULT_REPO_URL=...
SERVICENOW_DEFAULT_REPO_FULL_NAME=...
SERVICENOW_DEFAULT_BRANCH=main
```

## Run Locally

```powershell
uvicorn server:app --host 0.0.0.0 --port 8000 --reload
```

Run the L2 RCA agent separately and point `L2_RCA_AGENT_URL` at it:

```powershell
cd vm-l2-rca-agent
uvicorn main:app --host 0.0.0.0 --port 8001 --reload
```

Run the DB Fix agent separately and point `DB_FIX_AGENT_URL` at it from the L2 RCA agent:

```powershell
cd vm-db-fix-agent
uvicorn main:app --host 0.0.0.0 --port 8002 --reload
```
