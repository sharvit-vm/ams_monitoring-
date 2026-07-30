import requests

from l2_rca.config.settings import settings


class AgentExecutor:

    def execute(self, diagnosis):
        agent = diagnosis.recommended_agent

        if agent == "db_fix_agent":
            return self._execute_db_fix_agent(diagnosis)

        if agent == "manual_review":
            return {
                "status": "MANUAL_REVIEW",
                "reason": "No automated downstream agent is configured for this diagnosis.",
                "recommended_agent": agent,
                "problem_domain": diagnosis.problem_domain,
            }

        raise Exception(f"Unknown Agent: {agent}")

    def _execute_db_fix_agent(self, diagnosis):
        endpoint = settings.DB_FIX_AGENT_URL.rstrip("/") + "/api/v1/execute"
        payload = self._build_downstream_payload(diagnosis)

        try:
            response = requests.post(
                endpoint,
                json=payload,
                timeout=120
            )
        except requests.RequestException as exc:
            return {
                "status": "DOWNSTREAM_UNAVAILABLE",
                "agent": "db_fix_agent",
                "endpoint": endpoint,
                "request": payload,
                "error": str(exc)
            }

        response_payload = self._parse_response(response)
        if response.ok:
            return response_payload

        return {
            "status": "DOWNSTREAM_FAILED",
            "agent": "db_fix_agent",
            "endpoint": endpoint,
            "status_code": response.status_code,
            "request": payload,
            "response": response_payload
        }

    def _parse_response(self, response):
        if not response.text:
            return {"status_code": response.status_code}
        try:
            return response.json()
        except ValueError:
            return {
                "status_code": response.status_code,
                "body": response.text
            }

    def _build_downstream_payload(self, diagnosis):
        return {
            "ticket_id": diagnosis.ticket_id,
            "application": diagnosis.application,
            "technology": diagnosis.technology,
            "problem_domain": diagnosis.problem_domain,
            "recommended_agent": diagnosis.recommended_agent,
            "confidence": diagnosis.confidence,
            "reason": diagnosis.reason,
            "recommended_checks": diagnosis.recommended_checks,
            "status": diagnosis.status
        }
