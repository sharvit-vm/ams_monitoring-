import unittest

from l2_rca.llm.groq_client import GroqClient
from l2_rca.models.diagnosis import Diagnosis
from l2_rca.utils.output_parser import OutputParser


class DummyCompletions:
    def create(self, *args, **kwargs):
        raise RuntimeError("Connection error")


class DummyChat:
    def __init__(self):
        self.completions = DummyCompletions()


class GroqClientFallbackTests(unittest.TestCase):
    def test_diagnose_returns_structured_fallback_when_groq_fails(self):
        client = GroqClient()
        client.client = type("DummyClient", (), {"chat": DummyChat()})()

        content, latency_ms, token_usage = client.diagnose(
            {"title": "Database connection error", "description": "Queries are timing out"},
            ticket_id="INC-100",
        )

        diagnosis_payload = OutputParser.parse(content)
        diagnosis = Diagnosis(**diagnosis_payload)

        self.assertTrue(diagnosis.problem_domain)
        self.assertTrue(diagnosis.reason)
        self.assertGreaterEqual(latency_ms, 0)
        self.assertEqual(token_usage, 0)


if __name__ == "__main__":
    unittest.main()
