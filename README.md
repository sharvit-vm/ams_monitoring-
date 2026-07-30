# AMS Monitoring Intake + Categorisation Service

Single deployable FastAPI service for the first AMS diagnosis stage, with a lightweight dashboard for inspecting workflow execution state.

Flow:

```text
<<<<<<< Updated upstream
incident webhook -> connector -> normaliser -> local categorisation LangGraph node
=======
incident webhook -> connector -> normalizer -> categorisation -> optional L2 RCA agent -> optional fix agent -> ServiceNow notification
>>>>>>> Stashed changes
```

This service embeds the categorisation agent as the `categorization_layer` Python package. It does not call a separately deployed categorisation API.

## Endpoints

- `POST /webhook/incidents/{source}` where `source` is `jira`, `servicenow`, or `github`
- `POST /webhook/jira`
- `POST /webhook/servicenow`
- `POST /webhook/github`
- `GET /health`
- `GET /dashboard/workflow` to fetch the workflow graph metadata for the UI
- `GET /dashboard/executions/latest` to fetch the latest recorded execution summary

## Runtime Ownership

- `server.py` owns the FastAPI entry points and dashboard API responses.
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
