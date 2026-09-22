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
            analysis_facts={
                "observed_value_or_state": "null email",
                "representation_or_type": "null",
                "execution_path": ["ClientController -> findByEmail -> trim"],
                "failure_mechanism": "trim is called on null",
                "expected_behavior": "validate email before trimming",
                "source_evidence": ["ClientServiceImpl.java:49"],
            },
        )
        warnings = _semantic_validation_warnings(event, {}, report)
        self.assertTrue(any("remediation location" in warning for warning in warnings))
        report.fix_suggestion = "Validate email in ClientServiceImpl before trim()."
        self.assertEqual(_semantic_validation_warnings(event, {}, report), [])

    def test_source_backed_rca_requires_source_file_citation(self):
        event = ErrorEvent(
            id="test", fingerprint="test", error_type="ValueError", message="invalid value",
            file_path="src/parser.py", line_number=12,
        )
        report = L3RCAResult(
            root_cause="Invalid value reaches parser", buggy_file="src/parser.py",
            buggy_function="parse", buggy_lines=[12], affected_files=[],
            fix_suggestion="Validate the value before parsing.", confidence="medium",
            reasoning="The parser receives an invalid value.", evidence=["source evidence"],
            analysis_facts={
                "observed_value_or_state": "invalid value",
                "representation_or_type": "string",
                "execution_path": ["caller -> parse -> conversion"],
                "failure_mechanism": "conversion rejects the value",
                "expected_behavior": "validate before conversion",
                "source_evidence": ["other.py:12 - conversion call"],
            },
        )
        warnings = _semantic_validation_warnings(
            event, {"failing_range": "# File: src/parser.py\n12: parse(value)"}, report
        )
        self.assertTrue(any("verified failing source file" in warning for warning in warnings))

    def test_complete_structured_reasoning_facts_are_accepted(self):
        event = ErrorEvent(id="test", fingerprint="test", error_type="ValueError", message="invalid value")
        report = L3RCAResult(
            root_cause="Invalid value reaches parser", buggy_file="src/parser.py",
            buggy_function="parse", buggy_lines=[12], affected_files=[],
            fix_suggestion="Validate the value before parsing.", confidence="medium",
            reasoning="The parser receives an invalid value.", evidence=["src/parser.py:12"],
            analysis_facts={
                "observed_value_or_state": "invalid value",
                "representation_or_type": "string",
                "execution_path": ["caller -> parse -> conversion"],
                "failure_mechanism": "conversion rejects the value",
                "expected_behavior": "validate before conversion",
                "source_evidence": ["src/parser.py:12 - conversion call"],
                "uncertainties": [],
            },
        )
        warnings = _semantic_validation_warnings(
            event, {"failing_range": "# File: src/parser.py\n12: parse(value)"}, report
        )
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
