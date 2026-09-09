import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from contextlib import nullcontext

from agents.l3_rca.agent import _calculate_confidence, _verify_result, _build_user_message
from agents.l3_rca.schemas import L3RCAResult, DefectLocation
from agents.l3_rca.source_evidence import SourceEvidence
from agents.l3_rca.validation import CausalReview, assess_cause, remediation_block_reason
from issuelayer.intake.schemas import ErrorEvent


class EvidenceFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = self.temp.name
        self.path = Path(self.root, "a.py")
        self.path.write_text("def clean(value):\n    return value.strip()\n", encoding="utf-8")
        self.store = SourceEvidence(self.root, "snapshot")
        self.record = self.store.read("a.py", 1, 2)
        self.eid = self.record["evidence_id"]
        self.event = ErrorEvent(id="test", fingerprint="test", error_type="AttributeError",
                                message="value is None", traceback="clean(value)")
        self.result = L3RCAResult(
            root_cause="clean dereferences a missing value", buggy_file="a.py",
            buggy_function="clean", buggy_lines=[1, 2], affected_files=[],
            fix_suggestion="Validate value before calling strip", confidence="high",
            reasoning="None has no strip method", evidence=[],
            analysis_facts=dict(observed_value_or_state="None", representation_or_type="None",
                execution_path=["clean -> strip"], failure_mechanism="dereference",
                expected_behavior="handle missing value", source_evidence=["a.py"],
                evidence_ids=[self.eid], defect_locations=[dict(line=2,
                    justification="strip is invoked without checking value", evidence_ids=[self.eid])]))

    def review(self, remediation="ready", verdict="supported", **kwargs):
        return CausalReview(verdict=verdict, reasons=["Source explains the observed dereference"],
                            citation_indexes=[0], location_verdict="supported",
                            remediation_verdict=remediation,
                            remediation_reasons=[] if remediation == "ready" else ["Caller contract needs inspection"],
                            **kwargs)

    def test_source_owned_citations_and_precise_lines(self):
        self.assertEqual(self.store.bind(self.result), [])
        self.assertEqual(self.result.buggy_lines, [2])
        self.assertEqual(self.result.analysis_facts.citations[0].excerpt, self.record["excerpt"])
        self.assertEqual(self.store.read("a.py", 1, 2)["evidence_id"], self.eid)

    def test_upstream_unknown_does_not_override_supported_local_cause(self):
        self.store.bind(self.result)
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = self.review(
            upstream_uncertainties=["Origin of missing input is not reported"])
        review = assess_cause(self.event, self.result, self.root, reviewer)
        self.assertEqual(review["verdict"], "supported")
        self.assertTrue(review["upstream_uncertainties"])
        prompt = reviewer.with_structured_output.return_value.invoke.call_args.args[0]
        self.assertIn('"incident_evidence"', prompt)
        self.assertIn("is not proof that it executed", prompt)

    def test_empty_rag_does_not_block_independently_verified_cause(self):
        context = {"entry_status": "traceback-backed", "rag_result": {"hits": []}}
        with patch("agents.l3_rca.agent.agent_llm") as model, \
             patch("agents.l3_rca.agent._review_semantic_result", return_value=self.result):
            model.with_structured_output.return_value.invoke.return_value = self.review()
            _verify_result(self.event, context, self.result, self.root, self.store)
        self.assertEqual(context["causal_review"]["verdict"], "supported")
        self.assertEqual(context["causal_review"]["remediation_verdict"], "ready")

    def test_changed_snapshot_rejected(self):
        self.path.write_text("def clean(value):\n    return ''\n", encoding="utf-8")
        self.assertTrue(self.store.bind(self.result))
        self.assertEqual(self.result.analysis_facts.citations, [])

    def test_unknown_evidence_and_outside_path_rejected(self):
        self.result.analysis_facts.evidence_ids = ["invented"]
        self.assertTrue(self.store.bind(self.result))
        self.assertIn("error", self.store.read("../outside.py", 1, 1))
        self.assertIn("error", self.store.read("C:/outside.py", 1, 1))

    def test_read_budget_and_range_bounds(self):
        store = SourceEvidence(self.root, "snapshot", max_chars=4)
        self.assertIn("error", store.read("a.py", 1, 2))
        self.assertFalse(store.records)
        self.assertIn("error", self.store.read("a.py", 0, 2))
        self.assertIn("error", self.store.read("a.py", 100, 101))

    def test_location_gap_does_not_discard_verified_source(self):
        self.result.analysis_facts.defect_locations = [DefectLocation(
            line=99, justification="unsupported location", evidence_ids=[self.eid])]
        self.assertEqual(self.store.bind(self.result), [])
        self.assertTrue(self.store.location_errors)
        self.assertEqual(self.result.buggy_lines, [])
        self.assertTrue(self.result.analysis_facts.citations)

    def test_supported_cause_survives_unresolved_remediation(self):
        context = {"rag_result": {"hits": [{"file_path": "a.py"}]}, "connected_files": ["caller.py"]}
        original = self.result.root_cause
        with patch("agents.l3_rca.agent.agent_llm") as model, \
             patch("agents.l3_rca.agent._review_semantic_result", return_value=self.result):
            model.with_structured_output.return_value.invoke.return_value = self.review("needs_investigation")
            result = _verify_result(self.event, context, self.result, self.root, self.store)
        score, _, breakdown = _calculate_confidence(self.event, context, result)
        self.assertEqual(result.root_cause, original)
        self.assertEqual(breakdown["causal_review"]["verdict"], "supported")
        self.assertGreaterEqual(score, .8)
        result.confidence_breakdown = breakdown
        self.assertTrue(remediation_block_reason(result))

    def test_missing_exact_line_can_preserve_cause_but_blocks_patch(self):
        self.result.analysis_facts.defect_locations = []
        self.store.bind(self.result)
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = self.review()
        review = assess_cause(self.event, self.result, self.root, reviewer)
        self.assertEqual(review["verdict"], "supported")
        self.assertEqual(review["remediation_verdict"], "needs_investigation")

    def test_wrong_cause_remains_low(self):
        context = {"rag_result": {"hits": [{"file_path": "a.py"}]}}
        with patch("agents.l3_rca.agent.agent_llm") as model, \
             patch("agents.l3_rca.agent._review_semantic_result", return_value=self.result):
            model.with_structured_output.return_value.invoke.return_value = self.review(verdict="contradicted")
            result = _verify_result(self.event, context, self.result, self.root, self.store)
        self.assertLess(_calculate_confidence(self.event, context, result)[0], .5)

    def test_material_follow_up_is_bounded_and_available_on_second_review(self):
        Path(self.root, "caller.py").write_text("clean(None)\n", encoding="utf-8")
        request = dict(file_path="caller.py", start_line=1, end_line=1, reason="Establish input")
        context = {}
        with patch("agents.l3_rca.agent.agent_llm") as model, \
             patch("agents.l3_rca.agent._review_semantic_result", return_value=self.result):
            model.with_structured_output.return_value.invoke.side_effect = [
                self.review("needs_investigation", evidence_requests=[request] * 5), self.review()]
            _verify_result(self.event, context, self.result, self.root, self.store)
            second_prompt = model.with_structured_output.return_value.invoke.call_args.args[0]
        self.assertEqual(len(context["follow_up_evidence"]), 3)
        self.assertIn("clean(None)", second_prompt)
        self.assertEqual(len(context["review_history"]), 2)

    def test_source_evidence_not_lost_when_rag_text_is_long(self):
        prompt = _build_user_message(self.event, "snapshot", {
            "rag_context": "x" * 20000, "source_records": [self.record],
            "evidence_bundle": {"graph_context": {"status": "completed"}}})
        self.assertIn(self.eid, prompt)
        self.assertIn("Graph context summary", prompt)
        self.assertIn("Source Context Status: available", prompt)

    def test_approval_resume_preserves_verification_and_blocks_before_git(self):
        from agents import code_fix
        self.result.confidence_breakdown = dict(verification_version=2, evidence_validity="valid",
            causal_review={"verdict": "supported"},
            remediation_readiness={"verdict": "needs_investigation"})
        plan = SimpleNamespace(execution_context={"event": self.event.model_dump(),
            "rca": self.result.model_dump(), "knowledge_id": "snapshot", "repo_dir": self.root})
        with patch.object(code_fix, "_git") as git:
            output = code_fix._execute_code_fix_plan(plan)
        self.assertEqual(output["status"], "BLOCKED")
        git.assert_not_called()

    def test_old_reports_keep_existing_policy(self):
        self.assertEqual(remediation_block_reason(SimpleNamespace(confidence="medium")), "")

    def test_complete_agent_flow_binds_sources_and_saves_independent_decisions(self):
        from agents.l3_rca import agent as module
        context = {"rag_result": {"hits": [dict(file_path="a.py", start_line=1, end_line=2, score=1)]},
                   "evidence_bundle": {"graph_context": {"status": "completed", "connected_files": ["b.py"]}}}
        with patch.object(module, "_build_parallel_context", return_value=context), \
             patch.object(module, "create_agent") as create, \
             patch.object(module, "agent_llm") as model, \
             patch.object(module, "trace_span", return_value=nullcontext(None)):
            create.return_value.invoke.return_value = {"structured_response": self.result.model_dump()}
            model.with_structured_output.return_value.invoke.return_value = self.review()
            result = module.run_l3_rca(self.event, "snapshot", self.root)
        self.assertEqual(result.buggy_lines, [2])
        self.assertEqual(result.confidence_breakdown["causal_review"]["verdict"], "supported")
        self.assertEqual(result.confidence_breakdown["remediation_readiness"]["verdict"], "ready")
        self.assertTrue(result.confidence_breakdown["source_file_read"])
        self.assertTrue(any(r.get("file_sha256") for r in result.evidence_records))
        self.assertEqual(remediation_block_reason(result), "")

    def test_failed_correction_preserves_supported_cause_and_blocks_patch(self):
        context = {"connected_files": ["caller.py"]}
        with patch("agents.l3_rca.agent.agent_llm") as model:
            model.with_structured_output.return_value.invoke.side_effect = [
                self.review("needs_investigation"), TimeoutError()]
            result = _verify_result(self.event, context, self.result, self.root, self.store)
        self.assertEqual(context["causal_review"]["verdict"], "supported")
        self.assertEqual(context["causal_review"]["remediation_verdict"], "blocked")
        self.assertEqual(context["repair_status"]["error_type"], "TimeoutError")
        self.assertEqual(result.root_cause, "clean dereferences a missing value")


if __name__ == "__main__":
    unittest.main()
