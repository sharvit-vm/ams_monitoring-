import unittest
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

from observability import agent_trace
from agents.l3_rca.agent import _semantic_validation_warnings
from agents.l3_rca.schemas import L3RCAResult
from issuelayer.intake.schemas import ErrorEvent


class RCARuntimeTests(unittest.TestCase):
    def test_disabled_tracing_preserves_original_exception(self):
        error = NameError("original RCA error")
        with patch.object(agent_trace, "_get_langfuse_client", return_value=None):
            with self.assertRaises(NameError) as result:
                with agent_trace.trace_span(name="test"):
                    raise error
        self.assertIs(result.exception, error)

    def test_enabled_tracing_cannot_suppress_application_exception(self):
        manager = MagicMock()
        manager.__exit__.return_value = True
        client = MagicMock()
        client.start_as_current_observation.return_value = manager
        error = ValueError("original")
        with patch.object(agent_trace, "_get_langfuse_client", return_value=client):
            with self.assertRaises(ValueError) as result:
                with agent_trace.trace_span(name="test"):
                    raise error
        self.assertIs(result.exception, error)
        self.assertIs(manager.__exit__.call_args.args[1], error)

    def test_setup_failure_runs_body_once(self):
        client = MagicMock()
        client.start_as_current_observation.side_effect = RuntimeError("SDK unavailable")
        ran = []
        with patch.object(agent_trace, "_get_langfuse_client", return_value=client):
            with agent_trace.trace_span(name="test") as observation:
                ran.append(observation)
        self.assertEqual(ran, [None])

    def test_cleanup_failure_does_not_rerun_body_or_mask_error(self):
        manager = MagicMock()
        manager.__enter__.return_value.update.side_effect = RuntimeError("update failed")
        manager.__exit__.side_effect = RuntimeError("close failed")
        client = MagicMock()
        client.start_as_current_observation.return_value = manager
        ran = []
        with patch.object(agent_trace, "_get_langfuse_client", return_value=client):
            with agent_trace.trace_span(name="test"):
                ran.append(True)
            with self.assertRaisesRegex(ValueError, "original"):
                with agent_trace.trace_span(name="test"):
                    raise ValueError("original")
        self.assertEqual(ran, [True])

    def test_fix_location_guard_accepts_class_name_without_extension(self):
        event = ErrorEvent(id="test", fingerprint="test", error_type="NullPointerException", message="email is null")
        report = L3RCAResult(
            root_cause="Null email dereferenced", buggy_file="src/service/ClientServiceImpl.java",
            buggy_function="findByEmail", buggy_lines=[49],
            affected_files=["src\\controller\\ClientController.java"],
            fix_suggestion="Add input validation in ClientController before calling findByEmail.",
            confidence="high", reasoning="test", evidence=[],
        )
        warnings = _semantic_validation_warnings(event, {}, report)
        self.assertTrue(any("remediation location" in warning for warning in warnings))
        report.fix_suggestion = "Validate email in ClientServiceImpl before trim()."
        self.assertEqual(_semantic_validation_warnings(event, {}, report), [])


if __name__ == "__main__":
    unittest.main()
