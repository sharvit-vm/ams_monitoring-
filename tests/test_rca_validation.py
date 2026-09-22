import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agents.l3_rca.agent import _calculate_confidence, _review_semantic_result
from agents.l3_rca.prompts import build_l3_rca_system_prompt
from agents.l3_rca.schemas import L3RCAResult
from agents.l3_rca.validation import CausalReview, SourceCitation, assess_cause, validate_citations, citation_repair_evidence, reconcile_citations
from issuelayer.intake.schemas import ErrorEvent


class CausalValidationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = self.directory.name
        Path(self.root, "a.py").write_text("return value.strip()\n", encoding="utf-8")
        self.event = ErrorEvent(id="test", fingerprint="test", error_type="AttributeError",
                                message="value is None", file_path="a.py", line_number=1,
                                traceback="a.py:1")
        self.result = L3RCAResult(
            root_cause="Dereferencing None", buggy_file="a.py", buggy_function="clean",
            buggy_lines=[1], affected_files=[], fix_suggestion="Validate value in a.py",
            confidence="high", reasoning="The method is called on None", evidence=["a.py:1"],
            analysis_facts=dict(observed_value_or_state="None", representation_or_type="None",
                execution_path=["strip()"], failure_mechanism="dereference",
                expected_behavior="handle missing value", source_evidence=["a.py:1"],
                citations=[dict(file_path="a.py", start_line=1, end_line=1,
                                excerpt="return value.strip()")]))

    def test_prompt_renders(self):
        with patch("agents.l3_rca.prompts.build_skill_prompt", return_value=""):
            self.assertIn('"citations"', build_l3_rca_system_prompt())

    def test_real_citation_passes(self):
        self.assertEqual(validate_citations(self.result, self.root), [])

    def test_fabricated_excerpt_never_reaches_reviewer(self):
        self.result.analysis_facts.citations[0].excerpt = "return 42"
        reviewer = MagicMock()
        review = assess_cause(self.event, self.result, self.root, reviewer)
        self.assertEqual(review["verdict"], "insufficient_evidence")
        reviewer.with_structured_output.assert_not_called()

    def test_outside_path_and_invalid_range_are_rejected(self):
        citation = self.result.analysis_facts.citations[0]
        citation.file_path = "../outside.py"
        self.assertTrue(validate_citations(self.result, self.root))
        citation.file_path = "a.py"
        citation.end_line = 100
        self.assertTrue(validate_citations(self.result, self.root))

    def test_well_formed_but_wrong_cause_is_rejected(self):
        self.result.root_cause = "A database transaction deadlocked"
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = CausalReview(
            verdict="contradicted", reasons=["No transaction appears in the cited operation"],
            citation_indexes=[0])
        self.assertEqual(assess_cause(self.event, self.result, self.root, reviewer)["verdict"], "contradicted")

    def test_review_failure_abstains(self):
        reviewer = MagicMock()
        reviewer.with_structured_output.side_effect = TimeoutError()
        self.assertEqual(assess_cause(self.event, self.result, self.root, reviewer)["verdict"], "insufficient_evidence")

    def test_supported_review_requires_valid_reference(self):
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = CausalReview(
            verdict="supported", reasons=["Source supports the cause"], citation_indexes=[99])
        self.assertEqual(assess_cause(self.event, self.result, self.root, reviewer)["verdict"], "insufficient_evidence")

    def test_confidence_requires_support_even_with_complete_retrieval(self):
        context = {"failing_range": "# File: a.py\nreturn value.strip()",
                   "connected_files": ["b.py"]}
        self.assertLess(_calculate_confidence(self.event, context, self.result)[0], .5)
        context["causal_review"] = {"verdict": "supported"}
        self.assertGreaterEqual(_calculate_confidence(self.event, context, self.result)[0], .8)
        self.result.analysis_facts.uncertainties = ["Two equivalent guard implementations are possible"]
        self.assertGreaterEqual(_calculate_confidence(self.event, context, self.result)[0], .8)
        context["causal_review"] = {"verdict": "insufficient_evidence"}
        self.assertLess(_calculate_confidence(self.event, context, self.result)[0], .5)

    def test_separate_citations_cover_multiple_target_lines(self):
        Path(self.root, "a.py").write_text("first()\nmiddle()\nlast()\n", encoding="utf-8")
        self.result.buggy_lines = [1, 3]
        self.result.analysis_facts.citations = [
            SourceCitation(file_path="a.py", start_line=n, end_line=n, excerpt=text)
            for n, text in [(1, "first()"), (3, "last()")]]
        self.assertEqual(validate_citations(self.result, self.root), [])
        self.result.buggy_lines.append(2)
        self.assertIn("Verified citations do not cover the proposed fix location.",
                      validate_citations(self.result, self.root))

    def test_unique_excerpt_repair_uses_actual_lines_without_mutating_diagnosis(self):
        Path(self.root, "a.py").write_text("header\n    return value.strip()\nfooter\n", encoding="utf-8")
        evidence = citation_repair_evidence(self.result, self.root)
        self.assertEqual(evidence[0]["start_line"], 2)
        self.assertEqual(evidence[0]["end_line"], 2)
        self.assertEqual(evidence[0]["excerpt"], "    return value.strip()")
        self.assertTrue(evidence[0]["unique_excerpt_match"])
        self.assertEqual(self.result.analysis_facts.citations[0].start_line, 1)
        self.assertTrue(validate_citations(self.result, self.root))

    def test_duplicate_excerpts_are_not_automatically_relocated(self):
        Path(self.root, "a.py").write_text("return value.strip()\nreturn value.strip()\n", encoding="utf-8")
        evidence = citation_repair_evidence(self.result, self.root)
        self.assertFalse(evidence[0]["unique_excerpt_match"])
        self.assertEqual(reconcile_citations(self.result, self.root), [])

    def test_alignment_repairs_coordinates_but_preserves_causal_gate(self):
        Path(self.root, "a.py").write_text("header\n    return value.strip()\n", encoding="utf-8")
        self.result.buggy_lines = [2]
        changes = reconcile_citations(self.result, self.root)
        self.assertEqual(changes[0]["original"]["start_line"], 1)
        self.assertEqual(changes[0]["corrected"]["start_line"], 2)
        self.assertEqual(validate_citations(self.result, self.root), [])
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = CausalReview(
            verdict="contradicted", reasons=["Input unsupported"], citation_indexes=[0])
        self.assertEqual(assess_cause(self.event, self.result, self.root, reviewer)["verdict"], "contradicted")

    def test_alignment_does_not_replace_fabricated_text(self):
        self.result.analysis_facts.citations[0].excerpt = "return fabricated()"
        self.assertEqual(reconcile_citations(self.result, self.root), [])
        self.assertTrue(validate_citations(self.result, self.root))

    def test_repair_exception_is_persisted_without_exception_body(self):
        context = {}
        with patch("agents.l3_rca.agent.agent_llm") as model:
            model.with_structured_output.return_value.invoke.side_effect = ValueError("private request body")
            returned = _review_semantic_result(self.event, context, self.result, ["invalid"], self.root)
        self.assertIs(returned, self.result)
        self.assertEqual(context["repair_status"]["status"], "failed")
        self.assertEqual(context["repair_status"]["error_type"], "ValueError")
        self.assertNotIn("private", str(context["repair_status"]))
        breakdown = _calculate_confidence(self.event, context, self.result)[2]
        self.assertEqual(breakdown["repair_status"], context["repair_status"])

    def test_repair_does_not_read_outside_repository(self):
        self.result.analysis_facts.citations[0].file_path = "../outside.py"
        self.assertEqual(citation_repair_evidence(self.result, self.root), [])

    def test_repair_prompt_receives_exact_source(self):
        with patch("agents.l3_rca.agent.agent_llm") as model:
            model.with_structured_output.return_value.invoke.return_value = self.result
            _review_semantic_result(self.event, {}, self.result, ["bad citation"], self.root)
            prompt = model.with_structured_output.return_value.invoke.call_args.args[0][0].content
        self.assertIn('"unique_excerpt_match": true', prompt)
        self.assertIn("return value.strip()", prompt)

    def test_material_uncertainty_overrides_supported_verdict(self):
        self.result.analysis_facts.uncertainties = ["Input not established"]
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = CausalReview(
            verdict="supported", reasons=["Plausible"], citation_indexes=[0],
            material_uncertainties=["Input not established"])
        review = assess_cause(self.event, self.result, self.root, reviewer)
        self.assertEqual(review["verdict"], "insufficient_evidence")

    def test_alternative_fix_does_not_skip_causal_review(self):
        self.result.analysis_facts.uncertainties = ["Two equivalent guards are possible"]
        reviewer = MagicMock()
        reviewer.with_structured_output.return_value.invoke.return_value = CausalReview(
            verdict="supported", reasons=["Cause established; guard choice does not affect it"],
            citation_indexes=[0])
        review = assess_cause(self.event, self.result, self.root, reviewer)
        self.assertEqual(review["verdict"], "supported")
        reviewer.with_structured_output.assert_called_once()


if __name__ == "__main__":
    unittest.main()
